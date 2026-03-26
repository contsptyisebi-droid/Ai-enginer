"""
F1 25 UDP Telemetry Listener & Packet Decoder
Listens on port 20777 (EA Sports F1 standard).
Decodes Car Telemetry (ID=6), Car Status (ID=7), Lap Data (ID=2),
and Car Damage (ID=10) packets to maintain a live car state.
"""

import struct
import socket
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Packet IDs ───────────────────────────────────────────────────────────────
PACKET_LAP_DATA       = 2
PACKET_CAR_TELEMETRY  = 6
PACKET_CAR_STATUS     = 7
PACKET_CAR_DAMAGE     = 10

# ─── Tyre compound lookup ─────────────────────────────────────────────────────
TYRE_COMPOUND_NAMES = {
    16: "Hard",
    17: "Medium",
    18: "Soft",
    7:  "Intermediate",
    8:  "Wet",
    0:  "Unknown",
}

FUEL_MIX_NAMES = {
    0: "Lean",
    1: "Standard",
    2: "Rich",
    3: "Max",
}

ERS_DEPLOY_MODE_NAMES = {
    0: "None",
    1: "Medium",
    2: "Overtake",
    3: "Hotlap",
}

FIA_FLAG_NAMES = {
    -1: "Unknown",
    0:  "None",
    1:  "Green",
    2:  "Blue",
    3:  "Yellow",
}

# ─── Packet header ────────────────────────────────────────────────────────────
# < H B B B B B Q f I I B B
# 2+1+1+1+1+1+8+4+4+4+1+1 = 29 bytes
HEADER_FORMAT = "<HBBBBBQfIIBB"
HEADER_SIZE   = struct.calcsize(HEADER_FORMAT)  # 29


def parse_header(data: bytes) -> Optional[dict]:
    if len(data) < HEADER_SIZE:
        return None
    unpacked = struct.unpack_from(HEADER_FORMAT, data, 0)
    return {
        "packetFormat":              unpacked[0],
        "gameYear":                  unpacked[1],
        "gameMajorVersion":          unpacked[2],
        "gameMinorVersion":          unpacked[3],
        "packetVersion":             unpacked[4],
        "packetId":                  unpacked[5],
        "sessionUID":                unpacked[6],
        "sessionTime":               unpacked[7],
        "frameIdentifier":           unpacked[8],
        "overallFrameIdentifier":    unpacked[9],
        "playerCarIndex":            unpacked[10],
        "secondaryPlayerCarIndex":   unpacked[11],
    }


# ─── Lap Data (Packet ID = 2) ─────────────────────────────────────────────────
# 22 cars, each LapData entry (F1 2024/25 spec):
# I I H B H B H B H B f f f B B B B B b B B B B B B B B H H B f B
# lastLapMs(4) currLapMs(4) sec1Ms(2) sec1Min(1) sec2Ms(2) sec2Min(1)
# deltaFrontMs(2) deltaFrontMin(1) deltaLeaderMs(2) deltaLeaderMin(1)
# lapDist(4) totalDist(4) safetyCarDelta(4)
# pos(1) lapNum(1) pitStatus(1) pitStops(1) sector(1) invalid(1)
# penalties(1) warnings(1) cornCutWarn(1) dtPens(1) sgPens(1)
# gridPos(1) driverStatus(1) resultStatus(1) pitTimerActive(1)
# pitLaneMs(2) pitStopMs(2) pitServePen(1) speedTrap(4) speedTrapLap(1)
# = 56 bytes
LAP_DATA_FORMAT = "<IIHBHBHBHBfffBBBBBbBBBBBBBBHHBfB"
LAP_DATA_SIZE   = struct.calcsize(LAP_DATA_FORMAT)  # 56


def parse_lap_data(data: bytes, player_idx: int) -> Optional[dict]:
    offset = HEADER_SIZE
    entry_offset = offset + player_idx * LAP_DATA_SIZE
    if len(data) < entry_offset + LAP_DATA_SIZE:
        return None
    u = struct.unpack_from(LAP_DATA_FORMAT, data, entry_offset)
    return {
        "lastLapTimeMs":          u[0],
        "currentLapTimeMs":       u[1],
        "sector1TimeMs":          u[2],
        "sector1TimeMinutes":     u[3],
        "sector2TimeMs":          u[4],
        "sector2TimeMinutes":     u[5],
        "deltaToCarInFrontMs":    u[6],
        "deltaToCarInFrontMin":   u[7],
        "deltaToRaceLeaderMs":    u[8],
        "deltaToRaceLeaderMin":   u[9],
        "lapDistance":            u[10],
        "totalDistance":          u[11],
        "safetyCarDelta":         u[12],
        "carPosition":            u[13],
        "currentLapNum":          u[14],
        "pitStatus":              u[15],
        "numPitStops":            u[16],
        "sector":                 u[17],
        "currentLapInvalid":      u[18],
        "penalties":              u[19],
        "totalWarnings":          u[20],
        "cornerCuttingWarnings":  u[21],
        "numUnservedDTPens":      u[22],
        "numUnservedSGPens":      u[23],
        "gridPosition":           u[24],
        "driverStatus":           u[25],
        "resultStatus":           u[26],
        "pitLaneTimerActive":     u[27],
        "pitLaneTimeInLaneMs":    u[28],
        "pitStopTimerMs":         u[29],
        "pitStopShouldServePen":  u[30],
        "speedTrapFastestSpeed":  u[31],
        "speedTrapFastestLap":    u[32],
    }


# ─── Car Telemetry (Packet ID = 6) ───────────────────────────────────────────
# Per car: H f f f B b H B B H 4H 4B 4B H 4f 4B
# speed(2) throttle(4) steer(4) brake(4) clutch(1) gear(1) engineRPM(2)
# drs(1) revLightsPercent(1) revLightsBitValue(2) brakesTemp[4](8)
# tyresSurfaceTemp[4](4) tyresInnerTemp[4](4) engineTemp(2) tyresPressure[4](16) surfaceType[4](4)
# = 60 bytes per car
CAR_TELEM_FORMAT = "<HfffBbHBBH4H4B4BH4f4B"
CAR_TELEM_SIZE   = struct.calcsize(CAR_TELEM_FORMAT)  # 60


def parse_car_telemetry(data: bytes, player_idx: int) -> Optional[dict]:
    # After header there's an extra uint8 for mfdPanelIndex (1 byte)
    # and uint8 mfdPanelIndexSecondaryPlayer (1 byte)
    # and uint8 suggestedGear (1 byte) - these are at the END of the packet, not per-car
    offset = HEADER_SIZE
    entry_offset = offset + player_idx * CAR_TELEM_SIZE
    if len(data) < entry_offset + CAR_TELEM_SIZE:
        return None
    u = struct.unpack_from(CAR_TELEM_FORMAT, data, entry_offset)
    return {
        "speed":                    u[0],
        "throttle":                 u[1],
        "steer":                    u[2],
        "brake":                    u[3],
        "clutch":                   u[4],
        "gear":                     u[5],
        "engineRPM":                u[6],
        "drs":                      u[7],
        "revLightsPercent":         u[8],
        "revLightsBitValue":        u[9],
        "brakesTemperature":        list(u[10:14]),   # [RL, RR, FL, FR]
        "tyresSurfaceTemperature":  list(u[14:18]),   # [RL, RR, FL, FR]
        "tyresInnerTemperature":    list(u[18:22]),   # [RL, RR, FL, FR]
        "engineTemperature":        u[22],
        "tyresPressure":            list(u[23:27]),   # [RL, RR, FL, FR]
        "surfaceType":              list(u[27:31]),   # [RL, RR, FL, FR]
    }


# ─── Car Status (Packet ID = 7) ──────────────────────────────────────────────
# Per car: B B B B B f f f H H B B H B B B b f f f B f f f B
# = 55 bytes per car
CAR_STATUS_FORMAT = "<BBBBBfffHHBBHBBBbfffBfffB"
CAR_STATUS_SIZE   = struct.calcsize(CAR_STATUS_FORMAT)  # 55


def parse_car_status(data: bytes, player_idx: int) -> Optional[dict]:
    offset = HEADER_SIZE
    entry_offset = offset + player_idx * CAR_STATUS_SIZE
    if len(data) < entry_offset + CAR_STATUS_SIZE:
        return None
    u = struct.unpack_from(CAR_STATUS_FORMAT, data, entry_offset)
    return {
        "tractionControl":           u[0],
        "antiLockBrakes":            u[1],
        "fuelMix":                   u[2],
        "fuelMixName":               FUEL_MIX_NAMES.get(u[2], "Unknown"),
        "frontBrakeBias":            u[3],
        "pitLimiterStatus":          u[4],
        "fuelInTank":                u[5],
        "fuelCapacity":              u[6],
        "fuelRemainingLaps":         u[7],
        "maxRPM":                    u[8],
        "idleRPM":                   u[9],
        "maxGears":                  u[10],
        "drsAllowed":                u[11],
        "drsActivationDistance":     u[12],
        "actualTyreCompound":        u[13],
        "visualTyreCompound":        u[14],
        "tyreName":                  TYRE_COMPOUND_NAMES.get(u[14], "Unknown"),
        "tyresAgeLaps":              u[15],
        "vehicleFiaFlags":           u[16],
        "fiaFlagName":               FIA_FLAG_NAMES.get(u[16], "Unknown"),
        "enginePowerICE":            u[17],
        "enginePowerMGUK":           u[18],
        "ersStoreEnergy":            u[19],
        "ersDeployMode":             u[20],
        "ersDeployModeName":         ERS_DEPLOY_MODE_NAMES.get(u[20], "Unknown"),
        "ersHarvestedThisLapMGUK":   u[21],
        "ersHarvestedThisLapMGUH":   u[22],
        "ersDeployedThisLap":        u[23],
        "networkPaused":             u[24],
    }


# ─── Car Damage (Packet ID = 10) ─────────────────────────────────────────────
# Per car: 4f 4B 4B B B B B B B B B B B B B B B B B B B
# tyresWear[4](16) tyresDamage[4](4) brakesDamage[4](4)
# frontLeftWing(1) frontRightWing(1) rearWing(1) floor(1) diffuser(1)
# sidepod(1) drsFault(1) ersFault(1) gearBox(1) engine(1)
# engineMGUH(1) engineES(1) engineCE(1) engineICE(1) engineMGUK(1)
# engineTC(1) engineBlown(1) engineSeized(1)
# = 40 bytes per car
CAR_DAMAGE_FORMAT = "<4f4B4BBBBBBBBBBBBBBBBB"
CAR_DAMAGE_SIZE   = struct.calcsize(CAR_DAMAGE_FORMAT)  # 40


def parse_car_damage(data: bytes, player_idx: int) -> Optional[dict]:
    offset = HEADER_SIZE
    entry_offset = offset + player_idx * CAR_DAMAGE_SIZE
    if len(data) < entry_offset + CAR_DAMAGE_SIZE:
        return None
    u = struct.unpack_from(CAR_DAMAGE_FORMAT, data, entry_offset)
    return {
        "tyresWear":          list(u[0:4]),    # [RL, RR, FL, FR] 0-100%
        "tyresDamage":        list(u[4:8]),    # [RL, RR, FL, FR] 0-100%
        "brakesDamage":       list(u[8:12]),   # [RL, RR, FL, FR] 0-100%
        "frontLeftWingDmg":   u[12],
        "frontRightWingDmg":  u[13],
        "rearWingDmg":        u[14],
        "floorDmg":           u[15],
        "diffuserDmg":        u[16],
        "sidepodDmg":         u[17],
        "drsFault":           u[18],
        "ersFault":           u[19],
        "gearBoxDmg":         u[20],
        "engineDmg":          u[21],
        "engineMGUHWear":     u[22],
        "engineESWear":       u[23],
        "engineCEWear":       u[24],
        "engineICEWear":      u[25],
        "engineMGUKWear":     u[26],
        "engineTCWear":       u[27],
        "engineBlown":        u[28],
        "engineSeized":       u[29],
    }


# ─── Live Car State ───────────────────────────────────────────────────────────
@dataclass
class CarState:
    # Lap / Race
    position:             int   = 0
    current_lap:          int   = 0
    lap_distance:         float = 0.0
    pit_status:           int   = 0     # 0=none, 1=pitting, 2=in pit area
    num_pit_stops:        int   = 0
    penalties_sec:        int   = 0
    total_warnings:       int   = 0
    current_lap_invalid:  bool  = False
    fia_flag:             str   = "None"
    delta_to_leader_ms:   int   = 0
    delta_to_front_ms:    int   = 0
    grid_position:        int   = 0

    # Tyres
    tyre_name:            str   = "Unknown"
    tyre_age_laps:        int   = 0
    tyre_wear:            list  = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    tyre_damage:          list  = field(default_factory=lambda: [0, 0, 0, 0])
    tyre_surface_temp:    list  = field(default_factory=lambda: [0, 0, 0, 0])
    tyre_inner_temp:      list  = field(default_factory=lambda: [0, 0, 0, 0])
    tyre_pressure:        list  = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    # Fuel
    fuel_in_tank:         float = 0.0
    fuel_remaining_laps:  float = 0.0
    fuel_mix:             str   = "Standard"

    # ERS
    ers_store_energy:     float = 0.0   # Joules
    ers_deploy_mode:      str   = "None"
    ers_deployed_lap:     float = 0.0   # Joules

    # Power unit
    engine_power_ice:     float = 0.0
    engine_power_mguk:    float = 0.0

    # Car damage
    front_left_wing_dmg:  int   = 0
    front_right_wing_dmg: int   = 0
    rear_wing_dmg:        int   = 0
    floor_dmg:            int   = 0
    diffuser_dmg:         int   = 0
    sidepod_dmg:          int   = 0
    gearbox_dmg:          int   = 0
    engine_dmg:           int   = 0
    engine_blown:         bool  = False
    engine_seized:        bool  = False
    drs_fault:            bool  = False
    ers_fault:            bool  = False
    brakes_damage:        list  = field(default_factory=lambda: [0, 0, 0, 0])

    # Live driving
    speed_kmh:            int   = 0
    throttle:             float = 0.0
    brake:                float = 0.0
    gear:                 int   = 0
    engine_rpm:           int   = 0
    drs_active:           bool  = False
    engine_temp:          int   = 0

    # DRS
    drs_allowed:          bool  = False

    def to_context_string(self) -> str:
        """Return a compact telemetry snapshot for injecting into the LLM prompt."""
        wear = self.tyre_wear
        surf_temp = self.tyre_surface_temp
        labels = ["RL", "RR", "FL", "FR"]
        wear_str = ", ".join(f"{labels[i]}:{wear[i]:.1f}%" for i in range(4))
        temp_str = ", ".join(f"{labels[i]}:{surf_temp[i]}°C" for i in range(4))
        brakes_str = ", ".join(f"{labels[i]}:{self.brakes_damage[i]}%" for i in range(4))

        ers_pct = (self.ers_store_energy / 4_000_000 * 100) if self.ers_store_energy > 0 else 0.0

        lines = [
            f"Position: P{self.position} | Lap: {self.current_lap}",
            f"Tyre: {self.tyre_name} | Age: {self.tyre_age_laps} laps",
            f"Tyre Wear: {wear_str}",
            f"Tyre Surface Temps: {temp_str}",
            f"Fuel: {self.fuel_in_tank:.2f}kg | Fuel Remaining: {self.fuel_remaining_laps:.2f} laps | Mix: {self.fuel_mix}",
            f"ERS: {ers_pct:.1f}% | Mode: {self.ers_deploy_mode}",
            f"Speed: {self.speed_kmh} km/h | Gear: {self.gear} | RPM: {self.engine_rpm}",
            f"DRS: {'ON' if self.drs_active else 'OFF'} | DRS Allowed: {'Yes' if self.drs_allowed else 'No'}",
            f"Engine Temp: {self.engine_temp}°C | Engine Dmg: {self.engine_dmg}% | Blown: {self.engine_blown}",
            f"Wing Dmg - FL: {self.front_left_wing_dmg}% FR: {self.front_right_wing_dmg}% Rear: {self.rear_wing_dmg}%",
            f"Brakes Dmg: {brakes_str}",
            f"Gearbox Dmg: {self.gearbox_dmg}% | Pit Stops: {self.num_pit_stops}",
            f"Penalties: {self.penalties_sec}s | Warnings: {self.total_warnings}",
            f"Flag: {self.fia_flag} | Lap Invalid: {self.current_lap_invalid}",
            f"Delta to Leader: {self.delta_to_leader_ms / 1000:.3f}s | Delta to Car Ahead: {self.delta_to_front_ms / 1000:.3f}s",
        ]
        if self.engine_blown:
            lines.append("⚠ ENGINE BLOWN")
        if self.engine_seized:
            lines.append("⚠ ENGINE SEIZED")
        if self.drs_fault:
            lines.append("⚠ DRS FAULT")
        if self.ers_fault:
            lines.append("⚠ ERS FAULT")
        return "\n".join(lines)


# ─── Telemetry Listener ───────────────────────────────────────────────────────
class TelemetryListener:
    UDP_PORT = 20777
    BUFFER_SIZE = 2048

    def __init__(self, bind_address: str = "127.0.0.1"):
        self.bind_address = bind_address  # "127.0.0.1" = localhost only; "" = all interfaces
        self.state = CarState()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def get_state_snapshot(self) -> CarState:
        with self._lock:
            import copy
            return copy.copy(self.state)

    def _process_packet(self, data: bytes) -> None:
        header = parse_header(data)
        if header is None:
            return

        pid = header["packetId"]
        idx = header["playerCarIndex"]

        if pid == PACKET_LAP_DATA:
            parsed = parse_lap_data(data, idx)
            if parsed:
                with self._lock:
                    s = self.state
                    s.position           = parsed["carPosition"]
                    s.current_lap        = parsed["currentLapNum"]
                    s.lap_distance       = parsed["lapDistance"]
                    s.pit_status         = parsed["pitStatus"]
                    s.num_pit_stops      = parsed["numPitStops"]
                    s.penalties_sec      = parsed["penalties"]
                    s.total_warnings     = parsed["totalWarnings"]
                    s.current_lap_invalid = bool(parsed["currentLapInvalid"])
                    s.delta_to_leader_ms  = parsed["deltaToRaceLeaderMs"]
                    s.delta_to_front_ms   = parsed["deltaToCarInFrontMs"]
                    s.grid_position      = parsed["gridPosition"]

        elif pid == PACKET_CAR_TELEMETRY:
            parsed = parse_car_telemetry(data, idx)
            if parsed:
                with self._lock:
                    s = self.state
                    s.speed_kmh           = parsed["speed"]
                    s.throttle            = parsed["throttle"]
                    s.brake               = parsed["brake"]
                    s.gear                = parsed["gear"]
                    s.engine_rpm          = parsed["engineRPM"]
                    s.drs_active          = bool(parsed["drs"])
                    s.tyre_surface_temp   = parsed["tyresSurfaceTemperature"]
                    s.tyre_inner_temp     = parsed["tyresInnerTemperature"]
                    s.tyre_pressure       = parsed["tyresPressure"]
                    s.engine_temp         = parsed["engineTemperature"]

        elif pid == PACKET_CAR_STATUS:
            parsed = parse_car_status(data, idx)
            if parsed:
                with self._lock:
                    s = self.state
                    s.tyre_name           = parsed["tyreName"]
                    s.tyre_age_laps       = parsed["tyresAgeLaps"]
                    s.fuel_in_tank        = parsed["fuelInTank"]
                    s.fuel_remaining_laps = parsed["fuelRemainingLaps"]
                    s.fuel_mix            = parsed["fuelMixName"]
                    s.ers_store_energy    = parsed["ersStoreEnergy"]
                    s.ers_deploy_mode     = parsed["ersDeployModeName"]
                    s.ers_deployed_lap    = parsed["ersDeployedThisLap"]
                    s.engine_power_ice    = parsed["enginePowerICE"]
                    s.engine_power_mguk   = parsed["enginePowerMGUK"]
                    s.drs_allowed         = bool(parsed["drsAllowed"])
                    s.fia_flag            = parsed["fiaFlagName"]

        elif pid == PACKET_CAR_DAMAGE:
            parsed = parse_car_damage(data, idx)
            if parsed:
                with self._lock:
                    s = self.state
                    s.tyre_wear            = parsed["tyresWear"]
                    s.tyre_damage          = parsed["tyresDamage"]
                    s.brakes_damage        = parsed["brakesDamage"]
                    s.front_left_wing_dmg  = parsed["frontLeftWingDmg"]
                    s.front_right_wing_dmg = parsed["frontRightWingDmg"]
                    s.rear_wing_dmg        = parsed["rearWingDmg"]
                    s.floor_dmg            = parsed["floorDmg"]
                    s.diffuser_dmg         = parsed["diffuserDmg"]
                    s.sidepod_dmg          = parsed["sidepodDmg"]
                    s.gearbox_dmg          = parsed["gearBoxDmg"]
                    s.engine_dmg           = parsed["engineDmg"]
                    s.engine_blown         = bool(parsed["engineBlown"])
                    s.engine_seized        = bool(parsed["engineSeized"])
                    s.drs_fault            = bool(parsed["drsFault"])
                    s.ers_fault            = bool(parsed["ersFault"])

    def _listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        try:
            sock.bind((self.bind_address, self.UDP_PORT))
            logger.info(
                "UDP telemetry listener started on %s:%d",
                self.bind_address or "*",
                self.UDP_PORT,
            )
            while self._running:
                try:
                    data, _ = sock.recvfrom(self.BUFFER_SIZE)
                    self._process_packet(data)
                except socket.timeout:
                    continue
                except Exception as exc:
                    logger.debug("Packet error: %s", exc)
        except OSError as exc:
            logger.error("Failed to bind UDP socket on port %d: %s", self.UDP_PORT, exc)
        finally:
            sock.close()
            logger.info("UDP telemetry listener stopped")

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True, name="telemetry-udp")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
