# Hand-decoding one EXI message / 手工解码一条 EXI 消息

Day 9. Capture: [`session-02.pcap`](session-02.pcap) · 92 packets · 38 V2GTP
messages over TCP + 2 SDP over UDP.
Decoder: [uhi22/OpenV2Gx](https://github.com/uhi22/OpenV2Gx), built with
`cd Release && make`.

## The bytes, straight off the wire / 线路上的原始字节

Packet 1, the first thing the car says after the TCP handshake:

```
0x0040:                      01fe 8001 0000 0024
0x0050:  8000 ebab 9371 d34b 9b79 d189 a989 89c1
0x0060:  d191 d191 8189 99d2 6b9b 3a23 2b30 0200
0x0070:  0004 0040
```

| Bytes | Field | Value |
|---|---|---|
| `01` | Protocol Version | 1 |
| `fe` | Inverse Version | `~0x01` — a cheap misalignment check |
| `8001` | Payload Type | `EXI_ENCODED` |
| `00000024` | Payload Length | **36** |
| `8000ebab…0040` | Payload | 36 bytes of EXI |

TCP carried 44 bytes. `44 − 8 = 36`, matching the declared length — the framing
is self-consistent.
TCP 载荷 44 字节，`44 − 8 = 36`，和声明的长度一致 —— 分帧自洽。

> The payload's first byte `0x80` is the EXI header: distinguishing bits `10`,
> no options, version 1. Every EXI stream starts this way.
> 载荷第一个字节 `0x80` 是 EXI 头：识别位 `10`，无选项，版本 1。
> 每条 EXI 流都这么开头。

## Decoding it / 解码

```bash
./OpenV2G.exe DH8000ebab9371d34b9b79d189a98989c1d191d191818999d26b9b3a232b30020000040040
```

`D` = decode, `H` = the application-handshake schema. The schema letters are
`H`/`h` handshake req/res, `D` DIN 70121, `1` ISO 15118-2, `2` ISO 15118-20.
**The decoder must be told which schema to use** — schema-informed EXI carries
no field names, so the same bytes mean different things under different schemas.
**必须告诉解码器用哪个 schema** —— schema-informed EXI 不传字段名，
同样的字节在不同 schema 下含义完全不同。

```json
{
"msgName": "supportedAppProtocolReq",
"info": "36 bytes to convert",
"result": "Vehicle supports 1 protocols. ProtocolEntry#1 ProtocolNamespace=urn:iso:15118:2:2013:MsgDef Version=2.0 SchemaID=1 Priority=1 ",
"schema": "appHandshake",
"AppProtocol_arrayLen": "1",
"NameSpace_0": "urn:iso:15118:2:2013:MsgDef",
"Version_0": "2.0",
"SchemaID_0": "1",
"Priority_0": "1"
}
```

## Cross-check against the stack's own log / 和协议栈自己的日志对照

```
supportedAppProtocolReq: {"AppProtocol":[{"ProtocolNamespace":"urn:iso:15118:2:2013:MsgDef",
                          "VersionNumberMajor":2,"VersionNumberMinor":0,"SchemaID":1,"Priority":1}]}
```

Identical. The bytes captured off the interface, decoded by an unrelated C
implementation, reproduce exactly what the Python stack logged.
完全一致。从网卡上抓下来的字节，用一个毫不相关的 C 实现解码，
和 Python 协议栈打的日志分毫不差。

## What EXI actually buys / EXI 到底省了多少

| Form | Size | vs EXI |
|---|---|---|
| XML (what EXI encodes) | 360 B | −90 % |
| JSON (the log's rendering) | 169 B | −79 % |
| **EXI on the wire** | **36 B** | — |

The saving comes from *schema-informed* encoding: both ends already have the
XSD, so `ProtocolNamespace` is never transmitted — only "field 1 of this
element" and its value. Field names, the bulk of any XML or JSON document,
simply are not on the wire.
省下来的部分来自 *schema-informed* 编码：两端都已经有 XSD，所以
`ProtocolNamespace` 这个名字从不传输 —— 只传"本元素的第 1 个字段"和它的值。
字段名 —— XML 或 JSON 文档里体积的大头 —— 根本不在线路上。

The extreme case is packet 2, the response:

```bash
$ ./OpenV2G.exe DH80400040
{"msgName": "supportedAppProtocolRes",
 "result": "ResponseCode 0, SchemaID_isUsed 1, SchemaID 1"}
```

**Four bytes.** `OK_SuccessfulNegotiation` plus a schema id.
**四个字节。**

## Where OpenV2Gx stops / OpenV2Gx 的边界

Packet 12 — the 325-byte `ChargeParameterDiscoveryRes` carrying the
`SAScheduleList` — decodes only as far as the header:

```bash
$ ./OpenV2G.exe D1<325 bytes>
{"error": "runTheDecoder error-10", "schema": "ISO1",
 "header.SessionID": "65da8e1deb0a02e2", "header.Signature_isUsed": "1"}
```

The SessionID matches the SECC log (`65DA8E1DEB0A02E2`) exactly, so the bytes
were extracted correctly — OpenV2Gx's ISO1 decoder simply does not carry this
message all the way through. Worth knowing before relying on it as a reference
decoder: it is excellent for the handshake and for DIN 70121, and incomplete
for parts of ISO 15118-2.
SessionID 和 SECC 日志完全一致，说明字节提取没错 —— 是 OpenV2Gx 的 ISO1
解码器没把这条消息走完。把它当参考解码器之前值得知道这点：它对握手和
DIN 70121 很好用，对 ISO 15118-2 的部分消息不完整。
