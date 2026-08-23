-- V2GTP (V2G Transfer Protocol, ISO 15118-2 §7.3) dissector for Wireshark.
-- Wireshark 用的 V2GTP 解析器 (ISO 15118-2 第 7.3 节)。
--
-- Wireshark 4.2 ships no V2GTP dissector — only the IANA service name for
-- port 15118. This adds one: it splits the fixed 8-byte header and, more
-- usefully, checks the declared Payload Length against the bytes actually
-- present. TCP has no message boundaries, so that length field is the only
-- thing framing the stream; a mismatch is a real framing bug, and this makes
-- it visible instead of silent.
-- Wireshark 4.2 没有内置 V2GTP 解析器，只有端口 15118 的 IANA 服务名。
-- 这个补上：拆开固定 8 字节头，更重要的是把声明的 Payload Length 和实际
-- 到达的字节数做对比。TCP 没有消息边界，那个长度字段是唯一的分帧依据；
-- 对不上就是真的分帧问题，这里让它显形而不是静默。
--
-- Install / 安装:
--   mkdir -p ~/.local/lib/wireshark/plugins
--   cp v2gtp.lua ~/.local/lib/wireshark/plugins/
--   then restart Wireshark, or press Ctrl+Shift+L to reload Lua plugins.
--   然后重启 Wireshark，或按 Ctrl+Shift+L 重新加载 Lua 插件。

local v2gtp = Proto("v2gtp", "V2G Transfer Protocol")

local PAYLOAD_TYPES = {
  [0x8001] = "EXI_ENCODED",
  [0x9000] = "SDP_REQUEST",
  [0x9001] = "SDP_RESPONSE",
}

local f_version = ProtoField.uint8 ("v2gtp.version",         "Protocol Version",  base.HEX)
local f_inverse = ProtoField.uint8 ("v2gtp.inverse_version", "Inverse Version",   base.HEX)
local f_type    = ProtoField.uint16("v2gtp.payload_type",    "Payload Type",      base.HEX, PAYLOAD_TYPES)
local f_length  = ProtoField.uint32("v2gtp.payload_length",  "Payload Length",    base.DEC)
local f_payload = ProtoField.bytes ("v2gtp.payload",         "Payload")
-- SDP payloads are 2 bytes (request) / 20 bytes (response) and worth naming.
-- SDP 载荷是 2 字节(请求) / 20 字节(响应)，值得单独命名。
local f_security  = ProtoField.uint8("v2gtp.sdp.security",  "Security",  base.HEX,
                                     {[0x00] = "TLS", [0x10] = "No TLS"})
local f_transport = ProtoField.uint8("v2gtp.sdp.transport", "Transport", base.HEX,
                                     {[0x00] = "TCP", [0x10] = "UDP"})
local f_sdp_ip    = ProtoField.ipv6 ("v2gtp.sdp.address",  "SECC IPv6 Address")
local f_sdp_port  = ProtoField.uint16("v2gtp.sdp.port",    "SECC Port", base.DEC)

v2gtp.fields = {f_version, f_inverse, f_type, f_length, f_payload,
                f_security, f_transport, f_sdp_ip, f_sdp_port}

local e_badlength  = ProtoExpert.new("v2gtp.bad_length", "Payload Length disagrees with the bytes present",
                                     expert.group.MALFORMED, expert.severity.WARN)
v2gtp.experts = {e_badlength}

local function dissect(buf, pinfo, tree)
  local avail = buf:len()
  if avail < 8 then return 0 end

  local version = buf(0, 1):uint()
  local inverse = buf(1, 1):uint()
  -- The inverse-version byte is a cheap self-check: on a noisy PLC link a
  -- misaligned read fails here instead of deep inside the EXI decoder.
  -- 反码字节是个廉价的自检：噪声大的 PLC 链路上读错偏移会在这里就暴露，
  -- 而不是等到 EXI 解码器深处才崩。
  if version ~= 0x01 or inverse ~= 0xFE then return 0 end

  local ptype = buf(2, 2):uint()
  local plen  = buf(4, 4):uint()

  pinfo.cols.protocol = "V2GTP"
  pinfo.cols.info = string.format("%s, %d byte payload",
                                  PAYLOAD_TYPES[ptype] or string.format("0x%04X", ptype), plen)

  local st = tree:add(v2gtp, buf(0, avail), "V2G Transfer Protocol")
  st:add(f_version, buf(0, 1))
  st:add(f_inverse, buf(1, 1))
  st:add(f_type,   buf(2, 2))
  local len_item = st:add(f_length, buf(4, 4))

  local actual = avail - 8
  if actual ~= plen then
    len_item:add_proto_expert_info(e_badlength,
      string.format("declared %d, present %d — message split or coalesced across TCP segments",
                    plen, actual))
  end

  if actual > 0 then
    local body = buf(8, actual)
    if ptype == 0x9000 and actual >= 2 then
      local sdp = st:add(v2gtp, body, "SDP Request")
      sdp:add(f_security,  buf(8, 1))
      sdp:add(f_transport, buf(9, 1))
    elseif ptype == 0x9001 and actual >= 20 then
      local sdp = st:add(v2gtp, body, "SDP Response")
      sdp:add(f_sdp_ip,    buf(8, 16))
      sdp:add(f_sdp_port,  buf(24, 2))
      sdp:add(f_security,  buf(26, 1))
      sdp:add(f_transport, buf(27, 1))
    else
      -- EXI starts 0x80: distinguishing bits '10', no options, version 1.
      -- EXI 以 0x80 开头：识别位 '10'，无选项，版本 1。
      st:add(f_payload, body)
    end
  end
  return avail
end

function v2gtp.dissector(buf, pinfo, tree) return dissect(buf, pinfo, tree) end

-- SDP always uses UDP 15118, so register that outright. The V2G session runs
-- on a TCP port the SECC picks per session and announces in the SDP response,
-- so there is no fixed port to bind — a heuristic on the two version bytes is
-- the only way to catch it.
-- SDP 固定用 UDP 15118，直接注册。V2G 会话跑在 SECC 每次临时挑、并通过 SDP
-- 响应告知的 TCP 端口上，没有固定端口可绑 —— 只能靠那两个版本字节做启发式识别。
DissectorTable.get("udp.port"):add(15118, v2gtp)
v2gtp:register_heuristic("tcp", function(buf, pinfo, tree)
  return dissect(buf, pinfo, tree) > 0
end)
