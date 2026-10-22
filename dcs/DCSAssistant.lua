-- DCS Copilote v2 : envoie l'appareil piloté et sa situation de vol à l'app (UDP 127.0.0.1:7779), 1x/s.
-- À placer dans  Saved Games\DCS\Scripts\DCSAssistant.lua  et à appeler depuis Export.lua :
--   pcall(function() local l=require('lfs'); dofile(l.writedir()..[[Scripts\DCSAssistant.lua]]) end)
-- Compatible avec les autres exports (SRS, Tacview, Helios...) : les fonctions existantes sont chaînées.

local DCSA = { host = "127.0.0.1", port = 7779, interval = 1.0, next_t = 0, udp = nil }

local ok, socket = pcall(function()
  package.path  = package.path  .. ";.\\LuaSocket\\?.lua"
  package.cpath = package.cpath .. ";.\\LuaSocket\\?.dll"
  return require("socket")
end)
if not ok or not socket then
  log.write("DCSAssistant", log.ERROR, "LuaSocket introuvable")
  return
end

local prevStart = LuaExportStart
local prevAfter = LuaExportAfterNextFrame
local prevStop  = LuaExportStop

LuaExportStart = function()
  if prevStart then pcall(prevStart) end
  DCSA.udp = socket.udp()
  DCSA.udp:settimeout(0)
  DCSA.udp:setpeername(DCSA.host, DCSA.port)
end

LuaExportAfterNextFrame = function()
  if prevAfter then pcall(prevAfter) end
  local now = os.clock()
  if not DCSA.udp or now < DCSA.next_t then return end
  DCSA.next_t = now + DCSA.interval
  pcall(function()
    local me = LoGetSelfData()
    if not me or not me.Name then return end
    local name = string.gsub(me.Name, '["\\]', '')
    local parts = { string.format('"v":2,"aircraft":"%s"', name) }
    -- chaque valeur est lue séparément : un module ou un serveur peut en bloquer certaines
    local function add(key, fmt, fn)
      local ok, v = pcall(fn)
      if ok and type(v) == "number" and v == v then
        parts[#parts + 1] = string.format('"%s":' .. fmt, key, v)
      end
    end
    add("alt_m", "%.0f", LoGetAltitudeAboveSeaLevel)
    add("agl_m", "%.0f", LoGetAltitudeAboveGroundLevel)
    add("ias_ms", "%.1f", LoGetIndicatedAirSpeed)
    add("mach", "%.2f", LoGetMachNumber)
    add("vs_ms", "%.1f", LoGetVerticalVelocity)
    add("hdg_deg", "%.0f", function() return math.deg(me.Heading) end)
    add("aoa_deg", "%.1f", function() return math.deg(LoGetAngleOfAttack()) end)
    add("g", "%.1f", function() return LoGetAccelerationUnits().y end)
    add("fuel_kg", "%.0f", function() return LoGetEngineInfo().fuel_internal end)
    add("gear", "%.2f", function() return LoGetMechInfo().gear.value end)
    add("flaps", "%.2f", function() return LoGetMechInfo().flaps.value end)
    DCSA.udp:send("{" .. table.concat(parts, ",") .. "}")
  end)
end

LuaExportStop = function()
  if DCSA.udp then pcall(function() DCSA.udp:close() end) end
  DCSA.udp = nil
  if prevStop then pcall(prevStop) end
end
