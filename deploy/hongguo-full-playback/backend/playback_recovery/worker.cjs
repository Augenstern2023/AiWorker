// SPDX-License-Identifier: GPL-3.0-only
// Local playback integration, based on hongguoTV commit 77a99f509b8a5c586bf6ef453147cf1003203970.
// Credentials and signed URLs travel over private pipes only. Never log them.
const https = require('node:https');
const crypto = require('node:crypto');
const path = require('node:path');
const readline = require('node:readline');
const {spawnSync} = require('node:child_process');
const {registerPlaybackDevice} = require('./playback-device');
const fail = code => Object.assign(new Error(code), {code});
const MEDIA_HOSTS = ['qznovelvod.com','fqnovelvod.com','douyinvod.com'];
let identityPromise, cookie = '', denied = false;

function requestJson(url, options = {}, deadline, signal) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        if (u.protocol !== 'https:' || !['log5-applog-lf.fqnovel.com','api5-normal-sinfonlineb.fqnovel.com'].includes(u.hostname)) return reject(fail('HOST_REJECTED'));
        const data = options.data;
        const req = https.request(u, {method:options.method || 'GET', headers:{...options.headers,
            ...(data ? {'Content-Length':Buffer.byteLength(data)} : {})}, signal}, res => {
            const chunks = []; let count = 0;
            res.on('data', x => {count += x.length; if(count > 2*1024*1024) req.destroy(fail('TOO_LARGE')); else chunks.push(x)});
            res.on('error',reject);
            res.on('end', () => {
                if(res.statusCode !== 200) {denied = true; return reject(fail('HTTP_REJECTED'))}
                if(u.hostname === 'log5-applog-lf.fqnovel.com') cookie = (res.headers['set-cookie'] || []).map(x=>x.split(';')[0]).filter(x=>/^(install_id|ttreq|store-region|store-region-src)=/.test(x)).join('; ');
                const raw = Buffer.concat(chunks).toString('utf8');
                if(!raw.trim()) return reject(fail('EMPTY_RESPONSE'));
                try {resolve(JSON.parse(raw))} catch {reject(fail('INVALID_JSON'))}
            });
        });
        req.setTimeout(Math.max(1,deadline-Date.now()),()=>req.destroy(fail('ETIMEDOUT')));
        req.on('error',reject); if(data) req.write(data); req.end();
    });
}
function signed(identity, vid) {
    const input = [identity.device_id,identity.iid,identity.cdid,vid].map(x=>Buffer.from(x).toString('base64')).join('\n')+'\n';
    const cp = [path.join(__dirname,'playback.jar'),path.join(__dirname,'lib','kotlin-stdlib-2.2.20.jar'),path.join(__dirname,'lib','bcprov-jdk18on-1.79.jar')].join(path.delimiter);
    const java = process.env.HONGGUO_PLAYBACK_JAVA || path.join(__dirname,'..','jre','bin','java.exe');
    const p = spawnSync(java,['-cp',cp,'com.hongguotv.core.PlaybackCliKt'],{input,encoding:'utf8',timeout:15000,windowsHide:true,maxBuffer:128*1024});
    if(p.status !== 0) throw fail('SIGNER_FAILED');
    const result = {headers:{}};
    for(const line of p.stdout.trim().split(/\r?\n/)) {
        const a=line.split('\t'), dec=x=>Buffer.from(x,'base64').toString();
        if(a[0]==='URL') result.url=dec(a[1]);
        if(a[0]==='HEADER') result.headers[dec(a[1])]=dec(a[2]);
    }
    const u = new URL(result.url);
    if(u.searchParams.get('device_id')!==identity.device_id || u.searchParams.get('video_id')!==vid ||
        u.searchParams.get('_rticket')!==result.headers['x-ss-req-ticket'] || u.searchParams.has('ts') ||
        ['x-helios','x-medusa','x-medias','x-tt-dt'].some(k=>k in result.headers)) throw fail('REQUEST_MISMATCH');
    if(cookie) result.headers.Cookie=cookie;
    return result;
}
function parseModel(response, vid) {
    if(!response || Number(response.code ?? response.Code ?? -1)!==0) throw fail('BUSINESS_REJECTED');
    const map=response.data?.video_model_data || response.data;
    const row=map?.[vid]; // Exact episode key only; model.video_id is a separate CDN identity.
    let model=row?.video_model;
    if(typeof model==='string') {try {model=JSON.parse(model)} catch {throw fail('INVALID_MODEL')}}
    if(!model || typeof model!=='object' || Array.isArray(model)) throw fail('MISSING_EXACT_MODEL');
    if(!(Number(model.video_duration)>0)) throw fail('MISSING_DURATION');
    return model;
}
// URL decoding adapted from upstream src/native/app-source.js (same pinned commit).
function decryptSpadeUrl(encoded, seed) {
    const raw=Buffer.from(encoded,'base64');
    if(raw.length<20 || raw[0]!==0xa8 || raw[2]!==1 || raw[3]!==0) throw fail('INVALID_URL');
    const salt=Buffer.from('4dd4c2e6b83162090e52b3c7a6733ba41cb2462b829ab58a196b39db57177524f49baf7f08e8d68d26a72e37c1a95a2f1f05a51892aef2949732b62a38aadd58','hex');
    const h=crypto.createHash('sha512').update(Buffer.concat([crypto.createHash('sha512').update(seed).digest(),salt])).digest();
    const decipher=crypto.createDecipheriv('aes-128-cbc',h.subarray(0,16),h.subarray(16,32)); decipher.setAutoPadding(false);
    let out=Buffer.concat([decipher.update(raw.subarray(4,4+Math.floor((raw.length-4)/16)*16)),decipher.final()]);
    const padding=out[out.length-1]; if(padding>=1 && padding<=16 && padding<=out.length) out=out.subarray(0,out.length-padding);
    while(out.length && out[out.length-1]===0) out=out.subarray(0,out.length-1);
    return out.toString('utf8');
}
function mediaUrl(value, seed) {
    if(typeof value!=='string' || !value) return null;
    let raw=seed ? decryptSpadeUrl(value,Buffer.from(seed,'base64')) : value;
    if(!seed && !raw.startsWith('https://')) raw=Buffer.from(raw,'base64').toString('utf8');
    const u=new URL(raw.replace(/\\\//g,'/').replace(/\\u0026/g,'&').replace(/&amp;/g,'&'));
    if(u.protocol!=='https:' || u.username || u.password || (u.port && u.port!=='443') || !MEDIA_HOSTS.some(d=>u.hostname===d || u.hostname.endsWith('.'+d))) throw fail('MEDIA_HOST_REJECTED');
    return u.href;
}
function normalizeModel(model) {
    const tracks=[];
    for(const source of Object.values(model.video_list || {})) {
        if(!source || typeof source!=='object') continue;
        const codec=String(source.codec_type || source.video_meta?.codec_type || '').toLowerCase();
        if(!['h264','avc','avc1','hevc','h265','hvc1','hev1','bytevc1'].includes(codec)) continue;
        const main=mediaUrl(source.main_url || source.backup_url,model.key_seed);
        if(!main) continue;
        const enc={encrypt:source.encrypt,spade_a:source.spade_a,...source.encrypt_info};
        if(!enc.spade_a && ![false,0,'0','false'].includes(enc.encrypt)) throw fail('MISSING_ENCRYPTION_METADATA');
        enc.encrypt=!!enc.spade_a || ![false,0,'0','false'].includes(enc.encrypt);
        const meta={size:source.size,definition:source.definition,vwidth:source.vwidth||source.width,
            vheight:source.vheight||source.height,...source.video_meta,codec_type:codec==='bytevc1'?'hevc':codec};
        tracks.push({...source,main_url:main,backup_url:source.backup_url?mediaUrl(source.backup_url,model.key_seed):null,video_meta:meta,encrypt_info:enc});
    }
    if(!tracks.length) throw fail('NO_COMPATIBLE_SOURCE');
    const result={...model,video_list:tracks}; delete result.key_seed;
    return result;
}
async function getModel(vid) {
    if(!/^[0-9]{8,24}$/.test(vid)) throw fail('INVALID_EPISODE_ID');
    if(denied) throw fail('UPSTREAM_REJECTED');
    // One anonymous identity for this worker. No renewal/rotation on failure.
    identityPromise ||= registerPlaybackDevice(requestJson,{signal:AbortSignal.timeout(8000)});
    const identity=await identityPromise;
    const req=signed(identity,vid);
    const response=await requestJson(req.url,req,Date.now()+15000,AbortSignal.timeout(15000));
    try {return normalizeModel(parseModel(response,vid))}
    catch(e) {if(e.code==='BUSINESS_REJECTED') denied=true; throw e}
}
const SAFE_CODES=new Set(['HTTP_REJECTED','BUSINESS_REJECTED','UPSTREAM_REJECTED','EMPTY_RESPONSE','INVALID_JSON','TOO_LARGE','SIGNER_FAILED','REQUEST_MISMATCH','MISSING_EXACT_MODEL','INVALID_MODEL','MISSING_DURATION','INVALID_URL','MEDIA_HOST_REJECTED','MISSING_ENCRYPTION_METADATA','NO_COMPATIBLE_SOURCE','INVALID_EPISODE_ID','DEVICE_REGISTRATION_FAILED','ETIMEDOUT']);
if(require.main===module) {
    let chain=Promise.resolve();
    readline.createInterface({input:process.stdin}).on('line',line=>{chain=chain.then(async()=>{
        try {const req=JSON.parse(line); const model=await getModel(String(req.vid)); process.stdout.write(JSON.stringify({ok:true,model})+'\n')}
        catch(e) {process.stdout.write(JSON.stringify({ok:false,error:SAFE_CODES.has(e.code)?e.code:'PLAYBACK_FAILED'})+'\n')}
    })});
}
module.exports={parseModel,normalizeModel,signed,mediaUrl};
