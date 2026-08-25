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
- [x] Run the ISO 15118 SECC + EVCC from project 03 and save the log.
      `samples/v2g_session.log` (healthy) and `samples/v2g_session_fault.log`
      (5 A injected) — both captured in project 03, kept here so the tests are
      self-contained.
      两份日志都来自项目 03，拷进这里让测试自包含。
- [x] Fix the regex in `parsers/v2g_text.py` to match what it actually prints.
      根据它实际打印的内容修正正则。
      It targets the `exi_codec` lines, not the shorter `comm_session` ones,
      because only those carry the decoded payload — and `payload` is what a
      rule needs. 162 lines → 40 matches → 38 events, after filtering the
      xmldsig namespace and the `SalesTariff` fragment encoded for signing.
      盯的是 `exi_codec` 行而非更短的 `comm_session` 行，因为只有它们带载荷。
      162 行 → 匹配 40 → 过滤掉 xmldsig 和为签名单独编码的 SalesTariff → 38 个 Event。
- [x] Implement **R007** — the station advertises its ceiling twice, as `PMax`
      in the `SAScheduleList` and as `EVSEMaxCurrent` in
      `AC_EVSEChargeParameter`, and ISO 15118-2 sets no precedence between them.
      Compares `PMax` against `EVSEMaxCurrent × EVSENominalVoltage × √3`.
      A whole-session rule: the phase count is in the request, the limits in the
      response.
      桩把上限说了两遍而规范没规定谁优先。跨消息规则: 相数在请求里，限值在响应里。

      Fires on **both** captures — 32 A implies 22 170 W against a scheduled
      11 000 W, so the contradiction predates the injected fault. The healthy
      log is kept as a test case precisely so the rule cannot be fitted to the
      broken one.
      两份日志**都**报警 —— 矛盾在注入故障之前就存在。正常日志留作测试用例，
      正是为了防止规则被"照着故障日志凑出来"。
- [x] Implement **R008 (truncated session)** and **R010 (out-of-order session)**.
      R008 fires on [`samples/v2g_session_truncated.log`](samples/v2g_session_truncated.log),
      the healthy capture cut after its last `ChargingStatus` — what a pulled
      cable looks like in a log. It stays silent when charging never started,
      because there the refusal is the finding and R011 already reports it.
      R008 在截断样本上报警 —— 那是把正常抓包在最后一条 ChargingStatus 之后剪断，
      拔枪在日志里就长这样。充电从未开始时它不报，因为那里的问题是"被拒"本身。

      R010 checks **relative order only, never completeness**. The scaffold's
      reference list was DC-shaped and initial-capitalised, so judging the AC
      capture against it would have reported four skipped DC messages as
      deviations. There are now two orders sharing a common prefix, picked by
      which charging-loop message appeared, and optional messages may be absent.
      R010 **只查相对顺序，不查完整性**。脚手架里的参考表是直流形状、首字母大写的，
      拿它去判交流抓包会把四条合法跳过的直流消息报成偏差。现在是两张共享前缀的表，
      按充电循环用的是哪条消息来选，可选消息缺席不算偏差。
- [x] Extract `ResponseCode` from each `...Res` — anything not `OK_*` is a finding.
      **R011.** ISO 15118 has no CALLERROR frame, so `Kind.CALL_ERROR` never
      occurs in a V2G log and R002 can never fire on one; R003's `BAD_STATUS`
      is OCPP vocabulary and matches none of the `FAILED_*` codes. Without this
      rule a session that died on an expired certificate passes in silence.
      Whitelists success (4 OK codes in the session schema, 2 in the handshake)
      rather than blacklisting the 23 ways to fail.
      ISO 15118 没有 CALLERROR 帧，R002 在 V2G 上永远报不出东西；R003 的
      BAD_STATUS 是 OCPP 词汇，一个 FAILED_* 都匹配不上。用白名单列成功码，
      而不是黑名单列 23 种失败。

      ⚠️ Only exercised against synthetic responses so far — all three captured
      sessions succeeded. Injecting `FAILED_SequenceError` (option 3 in
      `03-iso15118-analysis/setup/run-secc-evcc.md`) would validate it for real.
      ⚠️ 目前只用合成响应验证过 —— 三份抓到的会话都成功了。注入
      `FAILED_SequenceError` 才能真正验证它。

### Day 12 — make it presentable / 让它可展示
- [x] Implement the SVG current plot (`render.py` TODO 1).
      What `SetChargingProfile` allowed, drawn against what `MeterValues`
      reported. In [`report-example-smart-charging.html`](samples/report-example-smart-charging.html)
      the station holds 32 A, the profile's second period starts at +30 s, and
      the measured current steps down to 16 A one sample later — the whole
      smart-charging story in one picture.
      `SetChargingProfile` 允许多少，对照 `MeterValues` 报了多少。示例里桩稳在 32 A，
      profile 第二个时段在 +30 秒开始，实测电流在下一个采样点降到 16 A ——
      智能充电的完整故事，一张图说完。

      SVG is text, so no plotting library and the standard-library-only rule
      holds. The limit is drawn as a step function, not a slope: it holds until
      the next period begins, and a straight line between periods would show a
      ramp the station never commanded. Returns `None` rather than empty axes
      when a log has no meter data — every V2G capture, and the hand-written
      OCPP faulty sample whose profile carries no `chargingSchedule`.
      SVG 就是文本，所以不需要绘图库，"只用标准库"那条规则守住了。限值画成阶梯而不是
      斜坡: 它保持到下一个时段开始，两点间画直线会画出桩从未下达过的渐变。
      日志没有电表数据时返回 `None` 而不是空坐标轴 —— 所有 V2G 抓包，
      以及手写的 OCPP 故障样本(它的 profile 里根本没有 chargingSchedule)。
- [x] Implement `--format html` (`render.py` TODO 2).
      One self-contained file — inline CSS, no CDN, no stylesheet, no script —
      because a report that needs the internet is not an attachment, it is a
      link that rots. A test asserts the absence of `http://`, `https://`,
      `<script src` and `<link `, so it cannot regress into needing a network.
      单个自包含文件 —— 样式内联，没有 CDN、外部样式表或脚本 ——
      因为一份需要联网的报告不是附件，是一条会失效的链接。有测试断言这四种
      外部引用都不存在，防止它退化成需要网络。

      The Mermaid diagram is embedded as source in a `<details>` block rather
      than rendered, for the same reason. Log content goes through
      `html.escape`, because it is untrusted input and markup in it must stay
      text.
      Mermaid 图以源码形式放在 `<details>` 里而不渲染，同理。日志内容经过
      `html.escape` —— 它是外部输入，里面的标签必须保持为文本。

      Generated: [`report-example.html`](samples/report-example.html) (OCPP),
      [`report-example-v2g.html`](samples/report-example-v2g.html),
      [`report-example-v2g-truncated.html`](samples/report-example-v2g-truncated.html).
- [x] Generate one report from a real broken session and commit it as
      `samples/report-example.md`. **This file is your interview artefact.**
      Two of them now: [`report-example.md`](samples/report-example.md) from the
      OCPP faulty sample (7 findings across 6 rules) and
      [`report-example-v2g.md`](samples/report-example-v2g.md) from the ISO 15118
      session with 5 A injected in project 03 (R007).
      现在有两份: OCPP 那份 7 条 findings 覆盖 6 条规则，V2G 那份是项目 03 里
      注入 5 A 的那次会话。
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
