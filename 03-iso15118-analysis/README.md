# 03 · ISO 15118 Session Analysis
# 03 · ISO 15118 会话分析

**Goal / 目标** — Run a real ISO 15118-2 DC charging session between a software
SECC and EVCC, capture it, break it on purpose, and write up what happened.
在软件 SECC 与 EVCC 之间跑一次真实的 ISO 15118-2 直流充电会话，抓包，
故意搞坏它，然后把发生的事写成报告。

**Why / 为什么** — Projects 01 and 02 prove you can build. This one proves you
can *read a protocol you had never seen two weeks ago*. That is the actual claim
you are making in the application.
项目 01 和 02 证明你会造东西。这个项目证明你能**读懂两周前从没见过的协议**——
这才是你在申请里真正想主张的能力。

⚠️ **This project has no code of its own.** It is running someone else's stack,
observing it carefully, and writing. That is deliberate: it is exactly what a
Werkstudent does in the first month.
⚠️ **这个项目没有自己的代码。** 它就是跑别人的协议栈、仔细观察、然后写。
这是刻意的: 这恰恰是 Werkstudent 头一个月要做的事。

---

## Contents / 内容

| Path | What |
|---|---|
| `setup/run-secc-evcc.md` | How to bring up SECC + EVCC with no hardware, and how to capture / 如何无硬件跑起两端并抓包 |
| `captures/` | Your `.pcap` and log files go here / 放你的抓包和日志 |
| `report/REPORT-TEMPLATE.md` | ⭐ The deliverable. Fill it in. / ⭐ 交付物，把它填满 |

---

## The 40-second version of ISO 15118 / 40 秒版本

```
1. PWM 5% duty (IEC 61851)   station says "I can do high-level communication"
2. SLAC        (15118-3)     which car is on MY cable? measure signal attenuation
3. HomePlug GreenPHY         a PLC network over the Control Pilot wire
4. IPv6 + SDP                car multicasts "where is the SECC?"
5. TCP / TLS
6. V2GTP                     8-byte header
7. EXI-encoded XML           the actual messages
```

Two facts that make you sound like you have done this / 两个能让你听起来像做过的事实:

1. **Why PLC and not WiFi** — the station must know *physically* which car is on
   *this* cable. SLAC measures signal attenuation across all candidates and picks
   the closest. Wireless cannot do that reliably in a car park.
   桩必须**物理上**确定是**这根线**上的哪台车。SLAC 测量所有候选者的信号衰减并选最近的。
   无线在停车场里做不到可靠配对。
2. **Why EXI and not plain XML** — `CurrentDemandReq/Res` runs at roughly 250 ms
   over a low-bandwidth PLC link. Plain XML would not fit the budget.
   `CurrentDemandReq/Res` 大约每 250 ms 一次，跑在低带宽 PLC 链路上，明文 XML 塞不下。

And one that most candidates get wrong / 还有一个大多数候选人会搞错的:

3. **DIN SPEC 70121 is not history.** It is the DC-only predecessor of
   ISO 15118-2, and most deployed DC chargers and cars actually negotiate *it*.
   A real SECC must support both and fall back automatically. If your lab session
   negotiates DIN 70121 instead of ISO 15118-2, that is not a bug — write it up.
   **DIN SPEC 70121 不是历史。** 它是 ISO 15118-2 的直流前身，
   现网大多数直流桩和车实际协商的是**它**。真实的 SECC 必须两个都支持并自动回退。
   如果你的实验会话协商成了 DIN 70121 而不是 ISO 15118-2，那不是 bug —— 把它写进报告。

---

## Step by step / 分步推进

### Day 8 — get a session to complete / 让一次会话跑完
- [x] Clone [EcoG-io/iso15118](https://github.com/EcoG-io/iso15118), read its README.
- [x] ~~Create the `veth` pair~~ — the repo ships a Docker path instead:
      `make build && make dev`. Two fixes were needed first, both recorded in
      [`setup/run-secc-evcc.md`](setup/run-secc-evcc.md).
      仓库自带 Docker 路径，比 `veth` 简单；但需要先打两个补丁。
- [x] Start SECC, start EVCC, get to `SessionStop` without errors.
      跑到 `SessionStop` 且无错误 —— 19 个请求，0 个错误。
- [x] Note which protocol was negotiated: **ISO 15118-2**, `AC_three_phase_core`.
      协商结果: **ISO 15118-2**，交流三相。
- [x] Save the full log to `captures/session-01-secc.log` and `-evcc.log`.
- [x] Write it up: 📄 [`report/REPORT-01.md`](report/REPORT-01.md).

### Day 9 — see the bytes / 看见字节
- [x] `tcpdump` the interface, open the pcap in Wireshark.
      Containers each have their own network namespace, so the host sees
      nothing — [`setup/capture-session.sh`](setup/capture-session.sh) captures
      from inside the SECC's namespace without sudo.
      容器各有独立网络命名空间，宿主机抓不到；脚本免 sudo 从 SECC 命名空间内抓。
- [x] Identify each layer: UDP 15118 (SDP) → TCP → V2GTP header → EXI blob.
      Wireshark 4.2 has no V2GTP dissector, so this repo ships one:
      [`tools/v2gtp.lua`](tools/v2gtp.lua). It also flags any packet whose
      declared Payload Length disagrees with the bytes present.
      Wireshark 4.2 没有 V2GTP 解析器，仓库自带一个，并会标出分帧不一致的包。
- [x] Build [OpenV2Gx](https://github.com/uhi22/OpenV2Gx) and decode one EXI
      message by hand. **Do this once — it demystifies the whole stack.**
      手动解码一条 EXI 消息。**做一次就够 —— 整个协议栈会瞬间去神秘化。**
      📄 [`captures/session-02-decoded.md`](captures/session-02-decoded.md)

### Day 10 — break it / 搞坏它
- [x] Inject one fault — option 1, the impossible power range, scripted for
      reproducibility: [`setup/faults/01-current-below-ev-minimum.sh`](setup/faults/01-current-below-ev-minimum.sh).
      One line of the SECC simulator, bind-mounted over the installed module so
      the upstream repo stays untouched.
      三选一里的第 1 个，写成脚本可复现；挂载覆盖，不改上游仓库。
- [x] Save the broken session:
      [`captures/session-03-fault.pcap`](captures/session-03-fault.pcap) +
      both container logs.
- [x] Trace the root cause by hand, before using any tool.
      在用任何工具之前，先手工追出根因。
      📄 [`report/REPORT-01.md` §5](report/REPORT-01.md)

      **Finding / 结论:** the EVCC never reads
      `AC_EVSEChargeParameter.EVSEMaxCurrent`. Offered 5 A against its own
      stated `EVMinCurrent` of 10 A, it requested 11 kW anyway — a
      `ChargingProfile` byte-identical to the healthy run. 38 messages, same
      lengths, zero errors, exit 0. The fault is invisible in Wireshark and
      lives in exactly **one** byte of a 325-byte message.
      EVCC 根本不读那个字段。被给了 5 A(自己声明最低 10 A)，它照样申请 11 kW，
      ChargingProfile 和正常会话逐字节相同。38 条消息、长度全同、零错误。
      Wireshark 里看不出任何异常，故障只存在于 325 字节里的**一个**字节。

### Day 11-12 — write it up / 写报告
- [ ] Fill in `report/REPORT-TEMPLATE.md`.
- [ ] Generate the Mermaid diagram and findings table with project 04.
- [ ] Plot requested vs allowed current across the session.
      画出整个会话中"车请求 vs 桩允许"的电流曲线。

---

## Talking points / 面试话术

1. **"I ran a full ISO 15118-2 DC session and then broke it on purpose."**
   Then describe the fault and the root-cause chain. Anyone can run a demo;
   the fault injection is the differentiator.
   讲你注入的故障和根因链。跑 demo 谁都会，注入故障才是区分度。
2. **"The session actually negotiated DIN 70121."** (If it did.) Explain why
   that is the normal case in the field. This one sentence signals real
   exposure. / 解释为什么现网里这才是常态。这一句就说明你真的接触过。
3. **"EXI is why V2G log analysis is a skill."** Bridges naturally into
   project 04. / 自然过渡到项目 04。

---

## References / 参考

- [EcoG-io/iso15118](https://github.com/EcoG-io/iso15118) — Python SECC + EVCC, `-2` / `-20` / `-8`
- [EVerest/everest-core](https://github.com/EVerest/everest-core) — industrial C++ charging stack
- [EVerest/libiso15118](https://github.com/EVerest/libiso15118) — C library, incl. `-20`
- [EDF-Lab/eVDriveFlow](https://github.com/EDF-Lab/eVDriveFlow) — ISO 15118-20 / bidirectional
- [uhi22/pyPLC](https://github.com/uhi22/pyPLC) — SLAC and the physical layer
- [uhi22/OpenV2Gx](https://github.com/uhi22/OpenV2Gx) — command-line EXI decoder
