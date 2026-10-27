"""Le script DCS (dcs/DCSAssistant.lua) exécuté dans Lua 5.1, comme dans DCS, avec un DCS simulé."""
import json
import locale
import os

import pytest

lua51 = pytest.importorskip("lupa.lua51")

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dcs", "DCSAssistant.lua")

# DCS simulé : LuaSocket qui garde les paquets, horloge réglable, données de vol d'un Hornet en approche
FAKE_DCS = """
sent, logs, calls, clock = {}, {}, {}, 0
log = { ERROR = 1, write = function(src, lvl, msg) logs[#logs + 1] = msg end }
os.clock = function() return clock end
package.preload["socket"] = function()
  return { udp = function()
    return { settimeout = function() end, setpeername = function() end,
             send = function(self, s) sent[#sent + 1] = s end, close = function() calls.closed = true end }
  end }
end
LuaExportStart = function() calls.prev_start = true end
LuaExportAfterNextFrame = function() calls.prev_after = (calls.prev_after or 0) + 1; error("SRS planté") end
LuaExportStop = function() calls.prev_stop = true end
LoGetSelfData = function() return { Name = 'FA-18C_hornet', Heading = math.rad(270) } end
LoGetAltitudeAboveSeaLevel = function() return 914.4 end
LoGetAltitudeAboveGroundLevel = function() return 300 end
LoGetIndicatedAirSpeed = function() return 77.2 end
LoGetMachNumber = function() return 0.24 end
LoGetVerticalVelocity = function() return -3.5 end
LoGetAngleOfAttack = function() return math.rad(8.1) end
LoGetAccelerationUnits = function() error("bloqué par le serveur") end
LoGetEngineInfo = function() return { fuel_internal = 2900 } end
LoGetMechInfo = function() return { gear = { value = 1 }, flaps = { value = 0.5 } } end
"""


@pytest.fixture(autouse=True)
def _nombres_comme_dans_dcs():
    """Points décimaux comme dans DCS : Qt applique la locale du système (fr_FR → « 77,2 » et JSON invalide)."""
    old = locale.setlocale(locale.LC_NUMERIC)
    locale.setlocale(locale.LC_NUMERIC, "C")
    yield
    locale.setlocale(locale.LC_NUMERIC, old)


def run(setup=FAKE_DCS):
    lua = lua51.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(setup)
    with open(SCRIPT, encoding="utf-8") as f:
        lua.execute(f.read())
    return lua


def test_paquet_envoye_et_exports_chaines():
    lua = run()
    g = lua.globals()
    g.LuaExportStart()
    g.LuaExportAfterNextFrame()
    g.clock = 0.5
    g.LuaExportAfterNextFrame()                       # moins d'une seconde : pas de nouveau paquet
    assert len(g.sent) == 1
    data = json.loads(g.sent[1])
    assert data == {"v": 2, "aircraft": "FA-18C_hornet", "alt_m": 914, "agl_m": 300, "ias_ms": 77.2, "mach": 0.24,
                    "vs_ms": -3.5, "hdg_deg": 270, "aoa_deg": 8.1, "fuel_kg": 2900, "gear": 1.0, "flaps": 0.5}
    assert "g" not in data                            # valeur bloquée : simplement absente, le reste passe
    assert g.calls.prev_start and g.calls.prev_after == 2   # SRS, Tacview… toujours appelés, même en erreur
    g.clock = 1.2
    g.LuaExportAfterNextFrame()
    g.LuaExportStop()
    assert len(g.sent) == 2 and g.calls.closed and g.calls.prev_stop


def test_sans_luasocket_le_script_se_retire():
    lua = run(FAKE_DCS.replace('package.preload["socket"]', 'local _unused'))
    g = lua.globals()
    assert list(g.logs.values()) == ["LuaSocket introuvable"]
    with pytest.raises(Exception, match="SRS planté"):
        g.LuaExportAfterNextFrame()                   # c'est bien l'export d'origine, intact
