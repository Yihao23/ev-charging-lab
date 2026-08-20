# Running an ISO 15118 SECC and EVCC locally
# 在本地跑起 ISO 15118 的 SECC 和 EVCC

No hardware needed. Both ends are software; they talk over a local network
interface instead of a PLC modem.
不需要硬件。两端都是软件，通过本地网络接口通信，而不是 PLC 调制解调器。

---

## Option A — EcoG-io/iso15118 (Python, recommended to start)
## 方案 A —— EcoG-io/iso15118 (Python，推荐入门)

```bash
git clone https://github.com/EcoG-io/iso15118.git
cd iso15118
cat README.md          # follow ITS instructions — they change | 按它自己的说明来，会变
```

Two things the README will make you decide / README 会让你做的两个决定:

| Decision | What to pick for a lab | 实验室里怎么选 |
|---|---|---|
| Network interface | a virtual pair, not a real NIC | 用虚拟网卡对，不要真网卡 |
| Protocol | start with `ISO_15118_2`, add `-20` later | 先 `ISO_15118_2`，之后再上 `-20` |

Create the virtual interface pair / 创建虚拟网卡对 (Linux):

```bash
sudo ip link add veth-evcc type veth peer name veth-secc
sudo ip link set veth-evcc up
sudo ip link set veth-secc up
# ISO 15118 runs over IPv6 link-local — both ends need one.
# ISO 15118 跑在 IPv6 链路本地地址上 —— 两端都要有。
ip -6 addr show veth-evcc
```

Then run the SECC on one interface and the EVCC on the other.
然后在一个接口上跑 SECC，另一个上跑 EVCC。

> If IPv6 link-local addresses do not appear, `sysctl net.ipv6.conf.all.disable_ipv6`
> is probably `1`. This is the single most common reason a V2G lab does not start.
> 如果没出现 IPv6 链路本地地址，多半是 `net.ipv6.conf.all.disable_ipv6=1`。
> 这是 V2G 实验环境起不来的头号原因。

---

## Option B — EVerest (C++, the industrial one)
## 方案 B —— EVerest (C++，工业级)

```bash
git clone https://github.com/EVerest/everest-demo.git
cd everest-demo
# it ships docker compose files for a full charging stack
# 它自带完整充电栈的 docker compose 配置
```

EVerest is what a real product looks like: `EvseManager`, `EvseV2G`
(DIN 70121 + ISO 15118-2), an OCPP module, an energy manager, all connected
over MQTT. Even if you never modify it, **draw its module topology** — that
diagram alone is worth bringing to the interview.
EVerest 是真实产品的样子: `EvseManager`、`EvseV2G`(DIN 70121 + ISO 15118-2)、
OCPP 模块、能量管理器，全部通过 MQTT 互联。即使你一行都不改，
**把它的模块拓扑画出来** —— 光这张图就值得带进面试。

---

## Option C — pyPLC (understand the physical layer)
## 方案 C —— pyPLC (理解物理层)

```bash
git clone https://github.com/uhi22/pyPLC.git
cd pyPLC && cat doc/hardware.md
```

You do not need the hardware to learn from this repo. Read how SLAC works,
and read `pcapConverter.py` — you will want the same trick in project 04.
不需要硬件也能从这个仓库学到东西。读懂 SLAC 的工作方式，
并读 `pcapConverter.py` —— 项目 04 里你会想用同样的技巧。

---

## Capturing the traffic / 抓包

```bash
# V2G application traffic is TCP over IPv6; SDP discovery is UDP 15118.
# V2G 应用层是 IPv6 上的 TCP; SDP 发现用 UDP 15118。
sudo tcpdump -i veth-secc -w ../captures/session-01.pcap
wireshark ../captures/session-01.pcap
```

What you will see, bottom to top / 你会自下而上看到:

```
1. UDP  port 15118      SECC Discovery Protocol (SDP)  —— "SECC 在哪?"
2. TCP  handshake        (+ TLS if Plug & Charge)
3. V2GTP                8-byte header: version, payload type, length
4. EXI                  a binary blob — Wireshark cannot decode it
```

Step 4 is the wall everyone hits. That is what `OpenV2Gx` is for:
第 4 步是所有人都会撞上的墙。`OpenV2Gx` 就是干这个的:

```bash
git clone https://github.com/uhi22/OpenV2Gx.git
cd OpenV2Gx && make
./OpenV2G_v0.9.5 EE <hex-blob>     # check the repo README for the exact flags
```

---

## Injecting a fault / 注入故障

This is the part that turns "I ran a demo" into "I debugged a protocol".
这一步把"我跑了个 demo"变成"我调试了一个协议"。

Pick one / 三选一:

1. **Impossible power range** — make the SECC answer `ChargeParameterDiscoveryRes`
   with an `EVSEMaximumCurrentLimit` far below what the EV asked for. Watch how
   the EVCC reacts. / 让 SECC 在 `ChargeParameterDiscoveryRes` 里返回远低于车请求的
   电流上限，观察 EVCC 怎么反应。
2. **CableCheck timeout** — delay the SECC's `CableCheckRes` past the spec
   timeout. The EV should abort the session. Find the exact timeout in the
   standard and confirm the observed behaviour matches.
   把 `CableCheckRes` 延迟到超过规范超时。车应该中止会话。去标准里找到确切的超时值，
   确认观察到的行为与之相符。
3. **Wrong ResponseCode** — return `FAILED_SequenceError` at a random point and
   trace how far the session unwinds. / 在随机位置返回 `FAILED_SequenceError`，
   追踪会话如何回退。

Save the log from the broken run. **It is the input for project 04.**
保存故障运行的日志。**它就是项目 04 的输入。**
