// SPDX-License-Identifier: GPL-3.0-only
// Local generic stdin bridge for hongguoTV 77a99f509b8a5c586bf6ef453147cf1003203970.
package com.hongguotv.core
import java.util.Base64
private fun b64(s: String) = Base64.getEncoder().encodeToString(s.toByteArray())
private fun un64(s: String) = String(Base64.getDecoder().decode(s), Charsets.UTF_8)
fun main(args: Array<String>) {
    if (args.firstOrNull() == "--selftest") {
        check(sm3("abc".toByteArray()).hex() == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0")
        val key = longArrayOf(1,2,3,4)
        val enc = Simon.encrypt(123,456,key)
        check(Simon.decrypt(enc[0],enc[1],key).contentEquals(longArrayOf(123,456)))
        val q = "aid=8662&device_id=123&version_name=7.3.9.32&_rticket=1700000000000"
        val a = Argus.sign(q,"",1700000000,8662,PlayerSigner.LICENSE_ID,"123","",1234567)
        check(Argus.recover551Field3(a) == 1234567)
        val h = PlayerSigner.headers(q,null,1700000000,1700000000000)
        check(h["x-ss-req-ticket"] == "1700000000000")
        check(decode64(h.getValue("x-argus")).size > 100)
        check(decode64(h.getValue("x-ladon")).size > 20)
        println("SELFTEST_OK")
        return
    }
    val lines = generateSequence(::readLine).toList()
    check(lines.size == 4)
    val (did,iid,cdid,vid) = lines.map(::un64)
    check(did.matches(Regex("[1-9][0-9]{0,29}")) && iid.matches(Regex("[1-9][0-9]{0,29}")))
    check(cdid.matches(Regex("[a-fA-F0-9-]{36}")))
    check(vid.matches(Regex("[0-9]{8,24}")))
    val profile = VendorConstants.device + mapOf("device_id" to did,"iid" to iid,"cdid" to cdid,
        "version_code" to "73932","manifest_version_code" to "73932","update_version_code" to "73932",
        "pv_player" to "73932","version_name" to "7.3.9.32")
    val extra = linkedMapOf("video_id" to vid,"video_id_type" to "0","device_level" to "1",
        "need_all_video_definition" to "1","need_mp4_align" to "0","use_os_player" to "0",
        "video_platform" to "1024","source" to "0","from_video_id" to "")
    val signed = Signer(device=profile).signPlayback(
        "https://api5-normal-sinfonlineb.fqnovel.com/reading/bookapi/video_tab/multi_video_model/v1/",profile+extra,null)
    val query = signed.url.substringAfter('?')
    val ticket = queryParam(query,"_rticket")!!.toLong()
    val real = PlayerSigner.headers(query,null,ticket/1000,ticket)
    val remove = setOf("x-gorgon","x-helios","x-medusa","x-argus","x-ladon","x-khronos","x-ss-stub","x-ss-req-ticket","x-medias","x-tt-dt")
    val headers = signed.headers.filterKeys { it.lowercase() !in remove } + real
    check(queryParam(query,"ts") == null)
    check(headers.getValue("x-ss-req-ticket") == ticket.toString())
    println("URL\t" + b64(signed.url))
    for((k,v) in headers) println("HEADER\t"+b64(k)+"\t"+b64(v))
}
