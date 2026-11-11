const assert=require('node:assert/strict');
const {parseModel,normalizeModel,signed,mediaUrl}=require('./worker.cjs');
const vid='1234567890123456789';
const model={video_id:'different-cdn-id',video_duration:183.6,video_list:[{
    codec_type:'bytevc1',main_url:Buffer.from('https://test.fqnovelvod.com/media').toString('base64'),
    encrypt:0,vwidth:1080,vheight:1920,size:100,definition:'1080p'}]};
assert.equal(parseModel({code:0,data:{video_model_data:{[vid]:{video_model:JSON.stringify(model)}}}},vid).video_id,'different-cdn-id');
assert.throws(()=>parseModel({code:0,data:{video_model_data:{other:{video_model:model}}}},vid),{code:'MISSING_EXACT_MODEL'});
assert.throws(()=>parseModel({code:100103,data:{}},vid),{code:'BUSINESS_REJECTED'});
assert.equal(normalizeModel(model).video_list[0].video_meta.codec_type,'hevc');
assert.throws(()=>normalizeModel({...model,video_list:[{...model.video_list[0],codec_type:'bytevc2'}]}),{code:'NO_COMPATIBLE_SOURCE'});
assert.throws(()=>mediaUrl('https://untrusted.example/video'),{code:'MEDIA_HOST_REJECTED'});
assert.throws(()=>normalizeModel({...model,video_list:[{...model.video_list[0],encrypt:1}]}),{code:'MISSING_ENCRYPTION_METADATA'});
const identity={device_id:'123456789012345',iid:'123456789012346',cdid:'12345678-1234-1234-1234-123456789012'};
for(const id of [vid,'9876543210123456789']) {
    const req=signed(identity,id); const u=new URL(req.url);
    assert.equal(u.searchParams.get('video_id'),id);
    assert.equal(u.searchParams.get('_rticket'),req.headers['x-ss-req-ticket']);
    assert(!u.searchParams.has('ts'));
    assert(Buffer.from(req.headers['x-argus'],'base64').length>100);
    assert(Buffer.from(req.headers['x-ladon'],'base64').length>20);
}
console.log('PASS: exact episode, refusals, codec filter, URL policy, encryption metadata, generic signed IDs/tickets');
