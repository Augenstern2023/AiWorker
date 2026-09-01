# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import base64
import binascii
import struct

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    def _aes_encrypt_block(key: bytes, block: bytes) -> bytes:
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        return encryptor.update(block) + encryptor.finalize()

except ImportError:  # pragma: no cover - only used by minimal deployments
    from Crypto.Cipher import AES

    def _aes_encrypt_block(key: bytes, block: bytes) -> bytes:
        return AES.new(key, AES.MODE_ECB).encrypt(block)


def derive_content_key(spade_b64: str) -> bytes:
    """Derive the 16-byte AES content key from captured SPADE material."""

    value = str(spade_b64 or "").strip()
    padding = (-len(value)) % 4
    if padding:
        value += "=" * padding
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("spade_a 不是有效的 Base64") from exc
    if len(raw) < 3:
        raise ValueError("spade_a 长度不足")
    transformed_length = len(raw) - (raw[0] ^ raw[1] ^ raw[2]) + 47
    if transformed_length <= 0 or transformed_length > len(raw) * 2:
        raise ValueError("spade_a 派生长度越界")
    if transformed_length + 1 > len(raw):
        transformed_length = len(raw) - 1
    if transformed_length < 33:
        raise ValueError("spade_a 有效载荷长度不足")
    payload = bytearray(raw[1 : 1 + transformed_length])
    previous_odd, previous_even = 85, 246
    for index in range(transformed_length):
        if index & 1:
            previous = previous_odd
            previous_odd = payload[index]
        else:
            previous = previous_even
            previous_even = payload[index]
        payload[index] = ((previous ^ payload[index]) - 21 - index.bit_count()) & 255
    try:
        key = binascii.unhexlify(bytes(payload[1:33]).decode("ascii"))
    except (UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("spade_a 未派生出有效 AES 密钥") from exc
    if len(key) != 16:
        raise ValueError("spade_a 派生密钥长度不是 16 字节")
    return key


def _find_box(data: bytes | bytearray, fourcc: bytes, start: int) -> tuple[int, int]:
    for index in range(max(4, start), len(data) - 4):
        if data[index : index + 4] != fourcc:
            continue
        size = struct.unpack(">I", data[index - 4 : index])[0]
        if 8 <= size < 5_000_000 and index - 4 + size <= len(data):
            return index - 4, size
    return -1, 0


def _box_body(data: bytes | bytearray, fourcc: bytes, start: int) -> bytes | None:
    offset, size = _find_box(data, fourcc, start)
    return bytes(data[offset + 8 : offset + size]) if offset >= 0 else None


def _parse_track(
    moov: bytes | bytearray, track_offset: int
) -> tuple[list[int], list[int], list[int], int, int, int] | None:
    table_offset, _ = _find_box(moov, b"stbl", track_offset + 8)
    if table_offset < 0:
        return None
    stsz = _box_body(moov, b"stsz", table_offset)
    stco = _box_body(moov, b"stco", table_offset)
    stsc = _box_body(moov, b"stsc", table_offset)
    saiz = _box_body(moov, b"saiz", table_offset)
    saio = _box_body(moov, b"saio", table_offset)
    if any(value is None for value in (stsz, stco, stsc, saiz, saio)):
        return None
    assert stsz is not None and stco is not None and stsc is not None
    assert saiz is not None and saio is not None
    if (
        len(stsz) < 12
        or len(stco) < 8
        or len(stsc) < 8
        or len(saiz) < 9
        or len(saio) < 12
    ):
        return None
    default_size = struct.unpack(">I", stsz[4:8])[0]
    sample_count = struct.unpack(">I", stsz[8:12])[0]
    if default_size:
        sizes = [default_size] * sample_count
    else:
        if len(stsz) < 12 + sample_count * 4:
            return None
        sizes = [struct.unpack(">I", stsz[12 + 4 * i : 16 + 4 * i])[0] for i in range(sample_count)]
    chunk_count = struct.unpack(">I", stco[4:8])[0]
    if len(stco) < 8 + chunk_count * 4:
        return None
    offsets = [struct.unpack(">I", stco[8 + 4 * i : 12 + 4 * i])[0] for i in range(chunk_count)]
    entry_count = struct.unpack(">I", stsc[4:8])[0]
    if len(stsc) < 8 + entry_count * 12:
        return None
    entries = [
        (
            struct.unpack(">I", stsc[8 + 12 * i : 12 + 12 * i])[0],
            struct.unpack(">I", stsc[12 + 12 * i : 16 + 12 * i])[0],
        )
        for i in range(entry_count)
    ]
    samples_per_chunk = [0] * chunk_count
    for index, (first_chunk, count) in enumerate(entries):
        end = entries[index + 1][0] - 1 if index + 1 < len(entries) else chunk_count
        for chunk in range(first_chunk - 1, min(end, chunk_count)):
            samples_per_chunk[chunk] = count
    default_aux_size = saiz[4]
    aux_count = struct.unpack(">I", saiz[5:9])[0]
    aux_offset = struct.unpack(">I", saio[8:12])[0]
    return sizes, offsets, samples_per_chunk, aux_offset, aux_count * max(default_aux_size, 8), sample_count


def _cenc_ctr(key: bytes, iv8: bytes, data: bytes) -> bytes:
    if len(key) != 16 or len(iv8) != 8:
        raise ValueError("CENC 需要 16 字节密钥和 8 字节 IV")
    counter = int.from_bytes(iv8 + bytes(8), "big")
    output = bytearray(data)
    for offset in range(0, len(data), 16):
        keystream = _aes_encrypt_block(key, counter.to_bytes(16, "big"))
        length = min(16, len(data) - offset)
        for index in range(length):
            output[offset + index] ^= keystream[index]
        counter += 1
    return bytes(output)


def _replace_sinf(data: bytearray) -> None:
    index = 4
    while index < len(data) - 4:
        if data[index : index + 4] != b"sinf":
            index += 1
            continue
        size = struct.unpack(">I", data[index - 4 : index])[0]
        if not 8 <= size < 50_000 or index - 4 + size > len(data):
            index += 1
            continue
        box_start = index - 4
        box_end = box_start + size
        frma_index = data.find(b"frma", index + 4, box_end)
        original_format = b""
        if frma_index >= index + 8 and frma_index + 8 <= box_end:
            frma_size = struct.unpack(">I", data[frma_index - 4 : frma_index])[0]
            if frma_size >= 12 and frma_index - 4 + frma_size <= box_end:
                original_format = bytes(data[frma_index + 4 : frma_index + 8])
        if len(original_format) == 4:
            search_start = max(0, box_start - 512)
            encrypted_positions = [
                data.rfind(marker, search_start, box_start)
                for marker in (b"encv", b"enca")
            ]
            entry_position = max(encrypted_positions)
            if entry_position >= 0:
                data[entry_position : entry_position + 4] = original_format
        data[index - 4 : index] = b"\x00\x00\x00\x08"
        data[index : index + 4] = b"free"
        data[index + 4 : index - 4 + size] = bytes(size - 8)
        index = index - 4 + size


def decrypt_mp4_cenc(data: bytes, content_key: bytes) -> bytes:
    """Decrypt the two standard audio/video CENC tracks in an MP4 payload."""

    if len(data) < 16 or data[4:8] != b"ftyp":
        raise ValueError("输入不是有效 MP4")
    output = bytearray(data)
    ftyp_size = struct.unpack(">I", output[:4])[0]
    moov_offset, moov_size = _find_box(output, b"moov", ftyp_size + 4)
    if moov_offset < 0:
        raise ValueError("MP4 缺少 moov")
    moov = output[moov_offset + 8 : moov_offset + moov_size]
    track_offset, track_size = _find_box(moov, b"trak", 4)
    track_offsets: list[int] = []
    while track_offset >= 0:
        track_offsets.append(track_offset)
        track_offset, track_size = _find_box(moov, b"trak", track_offset + track_size + 4)
    decrypted_samples = 0
    for offset in track_offsets:
        parsed = _parse_track(moov, offset)
        if parsed is None:
            continue
        sizes, chunk_offsets, samples_per_chunk, aux_offset, aux_size, sample_count = parsed
        if aux_offset + aux_size > len(output):
            continue
        auxiliary = output[aux_offset : aux_offset + aux_size]
        sample_index = 0
        aux_index = 0
        for chunk_index, chunk_offset in enumerate(chunk_offsets):
            sample_offset = chunk_offset
            for _ in range(samples_per_chunk[chunk_index]):
                if sample_index >= sample_count or aux_index + 8 > len(auxiliary):
                    break
                size = sizes[sample_index]
                if sample_offset + size > len(output):
                    break
                iv = bytes(auxiliary[aux_index : aux_index + 8])
                output[sample_offset : sample_offset + size] = _cenc_ctr(
                    content_key, iv, bytes(output[sample_offset : sample_offset + size])
                )
                sample_offset += size
                sample_index += 1
                aux_index += 8
                decrypted_samples += 1
    if decrypted_samples == 0:
        raise ValueError("MP4 中未找到可解密的 CENC 样本")
    _replace_sinf(output)
    # Well-formed protected sample entries carry their original codec in
    # sinf/frma. Retain the legacy fallbacks only for incomplete captures.
    output = output.replace(b"encv", b"hvc1").replace(b"enca", b"mp4a")
    return bytes(output)
