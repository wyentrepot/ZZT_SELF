"""REQS-0024 阶段0 实证脚本: 用真机样本核验位域布局假设(一次性,不入库)."""
import re, collections, zlib

FILES = [
    'reqs/0009-listener-flow-trace/samples/sample-A_并发抄表轮_406727-407600.txt',
    'reqs/0009-listener-flow-trace/samples/sample-B_28分钟处_426600-427300.txt',
    'reqs/0009-listener-flow-trace/samples/sample-C_5小时处_666100-666800.txt',
]

def load():
    for f in FILES:
        for line in open(f, encoding='utf-8', errors='replace'):
            m = re.search(r'7E FF 02 (.+?) 7E\s*$', line.strip())
            if m:
                yield bytes.fromhex(m.group(1).replace(' ', ''))

def get_tei(b, off):  # GET_TEI: 低8bit + 下字节低半字节
    return b[off] | ((b[off + 1] & 0x0F) << 8)

frames = list(load())
print('total frames:', len(frames))

# --- 1) GW 帧尾结构: 对 SACK(无载荷) 帧看 FCH 后还剩几字节 ---
sack_tails = collections.Counter()
for bs in frames:
    if (bs[20] & 7) == 2:
        sack_tails[len(bs) - 36] += 1
print('SACK FCH后剩余字节数分布:', dict(sack_tails))

# --- 2) SOF 帧: PB头 + MAC头校验 ---
teis_first = collections.Counter(); msdutype = collections.Counter()
len_resid = collections.Counter(); pbhead = collections.Counter()
sendtype_cnt = collections.Counter()
for bs in frames:
    if (bs[20] & 7) != 1:
        continue
    pbhead[bs[36]] += 1
    mac = bs[37:53]
    teis_first[(mac[0] >> 4)] += 1          # 表4 TEIs低4bit(期望CCO=1常见)
    msdutype[mac[7]] += 1
    msdulen = ((mac[9] & 7) << 8) | mac[8]
    # 假设: 20头+16FCH+1PB头+16MAC头+MSDU+ICV4 + 尾X
    len_resid[len(bs) - (37 + 16 + msdulen + 4)] += 1
    sendtype_cnt[mac[3] >> 4] += 1
print('PB头首字节分布(0x40=单块首片):', dict(sorted(pbhead.items())[:6]))
print('MAC头TEIs低4bit分布:', dict(teis_first))
print('MSDUtype分布(0=网管,48=0x30应用):', dict(sorted(msdutype.items())[:8]))
print('SOF长度余量分布(总长-37-16-MSDUlen-4):', dict(sorted(len_resid.items())[:8]))
print('发送类型分布(0单播/1全网/2本地/3代理):', dict(sendtype_cnt))

# --- 3) MSDUtype=0 网管消息: mmtype 分布 ---
mmt = collections.Counter(); tei_pair = collections.Counter()
for bs in frames:
    if (bs[20] & 7) != 1:
        continue
    mac = bs[37:53]
    if mac[7] != 0:
        continue
    msdulen = ((mac[9] & 7) << 8) | mac[8]
    msdu = bs[53:53 + msdulen]
    if len(msdu) < 2:
        continue
    mm = msdu[0] | (msdu[1] << 8)
    mmt[mm] += 1
    # 心跳表94: OSA=GET_TEI@0 应等于MAC头TEIs
    if mm == 0x0007:
        tei_pair[(mac[0] >> 4, msdu[2])] += 1
print('网管mmtype分布:', {hex(k): v for k, v in sorted(mmt.items())})
print('心跳 OSA(表94@0) vs MAC头TEIs低4bit 一致性:', dict(tei_pair))

# --- 4) 信标: table38头 + BPCS CRC32(尾部4B) ---
bcn_ok = bcn_bad = 0; bcncnt = collections.Counter(); bperiod = collections.Counter()
macs = collections.Counter()
for bs in frames:
    if (bs[20] & 7) != 0:
        continue
    pl = bs[36:]
    bcncnt[pl[0] & 7] += 1
    if (pl[0] & 7) != 2:
        continue
    macs[pl[2:8].hex().upper()] += 1
    bperiod[pl[8] | pl[9] << 8 | pl[10] << 16 | pl[11] << 24] += 1
    # 假设帧尾4B为GW footer, BPCS=payload最后4B: CRC32覆盖 pl[:-8]
    payload = pl[:-8]; bpcs = pl[-8:-4]
    if (zlib.crc32(payload) & 0xFFFFFFFF) == int.from_bytes(bpcs, 'little'):
        bcn_ok += 1
    else:
        bcn_bad += 1
print('信标类型分布(2=中央):', dict(bcncnt))
print('CCO MAC分布:', dict(macs.most_common(3)))
print('信标周期计数样本(前5):', sorted(bperiod)[:5])
print('BPCS CRC32(假设尾4B footer): ok=', bcn_ok, ' bad=', bcn_bad)
