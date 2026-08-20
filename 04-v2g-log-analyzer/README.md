# 04 · V2G / OCPP Log Analyzer
# 04 · V2G / OCPP 日志分析器

**Goal / 目标** — Turn a raw charging-session log into a timeline, a Mermaid
sequence diagram, and a ranked list of what went wrong.
把一份原始充电会话日志变成时间线、Mermaid 时序图和排序后的问题清单。

**Why this project matters most / 为什么这个项目最重要** — The job description
asks literally for *"Analyse von Logdaten sowie Unterstützung bei der
Fehlerdiagnose und Ursachenfindung"*. Every other candidate will say they are
willing to learn it. You bring the tool.
JD 里字面写着"日志数据分析以及故障诊断与根因定位"。别的候选人只会说"我愿意学"，
而你直接把工具带过去。

Standard library only. No `pip install`. It runs on a charging station's
embedded Linux as-is.
只用标准库，不需要 `pip install`，可以直接丢到充电桩的嵌入式 Linux 上跑。

---

## Quick start / 快速开始

```bash
cd 04-v2g-log-analyzer

# human-readable timeline + findings
python -m v2ganalyzer samples/ocpp_session_faulty.log

# a full Markdown report with an embedded Mermaid diagram
python -m v2ganalyzer samples/ocpp_session_faulty.log --format md -o report.md

# just the diagram
python -m v2ganalyzer samples/ocpp_session.log --format mermaid

# machine-readable, e.g. for the Node-RED flow in project 02
python -m v2ganalyzer samples/ocpp_session.log --format json

# CI gate: non-zero exit if any error-severity finding
python -m v2ganalyzer samples/ocpp_session.log --fail-on-error

python -m unittest discover -s tests -v     # 14 tests
```

---

## What it finds / 它能发现什么

Running it on the deliberately broken sample:
在故意做坏的样本上运行:

```
findings: 7
  [ERROR  ] R006     line 9  connector 1: illegal transition Available -> Charging
  [WARNING] R003    line 12  response carries idTagInfo.status=Invalid
  [WARNING] R003    line 14  response carries status=Rejected
  [WARNING] R004    line 15  MeterValues took 5500 ms (> 2000 ms)
  [ERROR  ] R002    line 18  CALLERROR NotSupported: vendorId is not registered
  [WARNING] R005    line 21  heartbeat gap of 135s (interval is 30s)
  [ERROR  ] R001    line 23  StopTransaction (uid=a9) was never answered
```

| Rule | Detects | 检测 |
|---|---|---|
| **R001** | A request that never got a reply | 发出后永远没有响应的请求 |
| **R002** | A protocol-level `CALLERROR` | 协议级错误帧 |
| **R003** | A reply carrying `Rejected` / `Invalid` / `Faulted` / … | 响应里带拒绝性状态 |
| **R004** | Response latency over a threshold | 响应时延超阈值 |
| **R005** | Heartbeat gap larger than the negotiated interval | 心跳间隔超过协商值 |
| **R006** | A `StatusNotification` sequence the OCPP state model forbids | OCPP 状态模型禁止的状态迁移序列 |

R005 is the one worth pointing at: it reads the interval out of the
`BootNotification` response in the same log, so the rule configures itself
rather than hard-coding 30 s.
R005 值得单独指出: 它从同一份日志里的 `BootNotification` 响应中读出心跳间隔，
规则自我配置，而不是写死 30 秒。

---

## Architecture / 架构

```
log file
   │
   ▼
parsers/          sniff() + parse()  ──►  list[Event]
   ├─ ocpp_json.py   OCPP-J frames          (done   | 已完成)
   └─ v2g_text.py    ISO 15118 messages     (SKELETON | 骨架，你来写)
   │
   ▼
correlate.py      pair CALL ↔ CALLRESULT, compute latency
   │
   ▼
rules.py          R001..R006  ──►  list[Finding]
   │
   ▼
render.py         markdown | mermaid | json | text
```

**The one design decision to defend in the interview / 面试要能为之辩护的设计决策:**
ISO 15118 and OCPP look nothing alike on the wire, but both are
request/response protocols over a stream. Map `...Req` → `CALL` and
`...Res` → `CALL_RESULT` and **every rule and renderer written for OCPP works
on V2G unchanged.** That is why `v2g_text.py` is 60 lines and not 600.
ISO 15118 和 OCPP 在线上毫无相似之处，但两者都是流式的请求/响应协议。
把 `...Req` 映射为 `CALL`、`...Res` 映射为 `CALL_RESULT`，
**为 OCPP 写的每一条规则和渲染器都能原封不动用在 V2G 上。**
这就是 `v2g_text.py` 只有 60 行而不是 600 行的原因。

`rules.py` deliberately re-declares the OCPP state table instead of importing
it from project 01: **a diagnostic tool must not depend on the implementation
it is diagnosing.** Say this out loud — it is a real engineering judgement.
`rules.py` 刻意重新声明状态表而不是从项目 01 import:
**诊断工具不能依赖它所诊断的实现。** 这句话要说出来，这是真正的工程判断。

---

## Step by step / 分步推进

### Day 10 — make it yours / 让它变成你的
- [ ] Read `models.py`, then `ocpp_json.py`. Understand why parsing and rules
      are separate. / 读懂为什么解析与规则要分离。
- [ ] Run project 01 again, capture your own log, and analyse it.
      重跑项目 01，抓自己的日志来分析。
- [ ] Break project 01 on purpose (reject the profile, drop a response) and
      confirm the analyzer catches it. / 故意搞坏项目 01，确认分析器能抓到。

### Day 11 — the V2G parser / V2G 解析器
- [ ] Run the ISO 15118 SECC + EVCC from project 03 and save the log.
- [ ] Fix the regex in `parsers/v2g_text.py` to match what it actually prints.
      根据它实际打印的内容修正正则。
- [ ] Implement **R007 (out-of-order session)** and **R008 (truncated session)**.
- [ ] Extract `ResponseCode` from each `...Res` — anything not `OK_*` is a finding.

### Day 12 — make it presentable / 让它可展示
- [ ] Implement the SVG current plot (`render.py` TODO 1).
- [ ] Implement `--format html` (`render.py` TODO 2).
- [ ] Generate one report from a real broken session and commit it as
      `samples/report-example.md`. **This file is your interview artefact.**
      从一次真实的故障会话生成报告并提交。**这个文件就是你的面试作品。**

---

## Talking points / 面试话术

1. **"I wrote a protocol-agnostic session analyzer."** Then explain the
   Req→CALL mapping. It shows you can see the shape shared by two protocols
   that look unrelated. / 讲 Req→CALL 的映射，证明你能看穿两个看似无关协议的共同结构。
2. **"R006 catches bugs from the outside."** A station whose own code thinks
   it is fine still emits an illegal `StatusNotification` sequence, and the
   log proves it. / 桩自己觉得没问题，日志照样能证明它错了。
3. **"Standard library only, so it runs on the target."** A diagnostic tool
   that needs a package manager is a tool you cannot use on site.
   需要包管理器的诊断工具，在现场就是用不了的工具。

---

## References / 参考

- [uhi22/OpenV2Gx](https://github.com/uhi22/OpenV2Gx) — command-line EXI decoder, call it from `v2g_text.py`
- [uhi22/pyPLC](https://github.com/uhi22/pyPLC) — see `pcapConverter.py` for pcap handling
- [EcoG-io/iso15118](https://github.com/EcoG-io/iso15118) — the stack that produces the V2G logs
