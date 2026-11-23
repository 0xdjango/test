#!/usr/bin/env python3
"""
HP ZBook Fury 16 - used laptop test suite (Windows 10/11)

Pure standard library (tkinter ships with python.org Windows installer).
Run from an ADMIN terminal for full results:

    python zbook_fury16_tester.py                 # everything
    python zbook_fury16_tester.py --quick         # short stress times
    python zbook_fury16_tester.py --only cpu,battery
    python zbook_fury16_tester.py --skip stress,gpu
    python zbook_fury16_tester.py --no-interactive
    python zbook_fury16_tester.py --list

Reports are saved to the Desktop as .txt and .json.
Optional extras if installed (used automatically): nvidia-smi, smartctl, stress-ng.
Also run separately: HWiNFO64 (sensors), FurMark/OCCT (GPU), MemTest86 (RAM, bootable).
"""
import argparse, ctypes, datetime, json, multiprocessing as mp, os, re, shutil
import subprocess, sys, tempfile, time

# ----------------------------------------------------------------- helpers
PASS, WARN, FAIL, INFO, SKIP = "PASS", "WARN", "FAIL", "INFO", "SKIP"
COLORS = {PASS: "\033[92m", WARN: "\033[93m", FAIL: "\033[91m", INFO: "\033[96m", SKIP: "\033[90m"}
RESET = "\033[0m"
ARGS = None
RESULTS = []          # (section, status, name, detail)
CURRENT = ["?"]


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def enable_ansi():
    try:
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:
        pass


def rec(status, name, detail=""):
    RESULTS.append((CURRENT[0], status, name, str(detail)))
    print(f"  {COLORS[status]}[{status}]{RESET} {name}" + (f" - {detail}" if detail else ""))


def ps(cmd, timeout=60):
    """Run PowerShell, return stdout text ('' on failure)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def psj(cmd, timeout=60):
    """PowerShell -> python list of dicts."""
    out = ps(f"{cmd} | ConvertTo-Json -Depth 4 -Compress", timeout)
    if not out:
        return []
    try:
        d = json.loads(out)
    except Exception:
        return []
    return d if isinstance(d, list) else [d]


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)


def ask_yn(q):
    """True / False / None(skipped or non-interactive)."""
    if ARGS.no_interactive:
        return None
    while True:
        a = input(f"  ? {q} [y/n/s=skip]: ").strip().lower()
        if a in ("y", "yes"):
            return True
        if a in ("n", "no"):
            return False
        if a in ("s", "skip", ""):
            return None


def pause(msg):
    if not ARGS.no_interactive:
        input(f"  > {msg} (press Enter) ")


def yn_result(name, q, fail_status=FAIL):
    a = ask_yn(q)
    if a is None:
        rec(SKIP, name, "not answered")
    else:
        rec(PASS if a else fail_status, name, "user confirmed" if a else "user reported problem")
    return a


# ----------------------------------------------------------------- tests
def t_sysinfo():
    cs = (psj("Get-CimInstance Win32_ComputerSystem") or [{}])[0]
    bios = (psj("Get-CimInstance Win32_BIOS") or [{}])[0]
    bb = (psj("Get-CimInstance Win32_BaseBoard") or [{}])[0]
    os_ = (psj("Get-CimInstance Win32_OperatingSystem") or [{}])[0]
    model = f"{cs.get('Manufacturer', '?')} {cs.get('Model', '?')}"
    rec(INFO, "Model", model)
    rec(INFO, "Serial", bios.get("SerialNumber", "?"))
    rec(INFO, "BIOS", f"{bios.get('SMBIOSBIOSVersion')} ({bios.get('Manufacturer')})")
    rec(INFO, "Board", f"{bb.get('Product')} {bb.get('SerialNumber')}")
    rec(INFO, "Windows", f"{os_.get('Caption')} build {os_.get('BuildNumber')}")
    if "fury 16" in model.lower():
        rec(PASS, "Model is ZBook Fury 16")
    else:
        rec(WARN, "Model is not reported as ZBook Fury 16", model)
    rec(INFO, "Check serial at support.hp.com (warranty, original config, product ID)")
    sb = ps("try{Confirm-SecureBootUEFI}catch{'unknown'}")
    rec(PASS if sb == "True" else WARN, "Secure Boot", sb)
    tpm = (psj("Get-Tpm") or [{}])[0]
    if tpm.get("TpmPresent"):
        rec(PASS if tpm.get("TpmReady") else WARN, "TPM", f"present, ready={tpm.get('TpmReady')}")
    else:
        rec(WARN, "TPM not detected (or not admin)")
    act = ps("(Get-CimInstance SoftwareLicensingProduct -Filter \"PartialProductKey IS NOT NULL AND "
             "Name LIKE 'Windows%'\" | Select -First 1).LicenseStatus")
    rec(PASS if act == "1" else WARN, "Windows activation", "licensed" if act == "1" else f"status={act or '?'}")
    # corporate lock checks - very important on used business laptops
    ds = ps("dsregcmd /status")
    flags = {k: bool(re.search(rf"{k}\s*:\s*YES", ds)) for k in ("AzureAdJoined", "DomainJoined", "EnterpriseJoined")}
    mdm = re.search(r"MdmUrl\s*:\s*(\S+)", ds)
    if any(flags.values()) or mdm:
        rec(FAIL, "Device is joined to org / MDM (Azure AD, domain or Intune)",
            f"{flags} MDM={mdm.group(1) if mdm else None} -> must be released by previous owner")
    else:
        rec(PASS, "Not joined to Azure AD / domain / MDM")
    bl = psj("Get-BitLockerVolume") if is_admin() else []
    for v in bl:
        rec(INFO, f"BitLocker {v.get('MountPoint')}", f"protection={v.get('ProtectionStatus')}")
    rec(INFO, "MANUAL: in BIOS (F10) verify no BIOS/HP Sure Admin password, no Absolute/Computrace lock, "
              "asset tag/ownership tag cleared")
    # uptime reinstall hint
    boot = os_.get("LastBootUpTime")
    if boot:
        rec(INFO, "Last boot", boot)


def t_devices():
    bad = psj("Get-PnpDevice -PresentOnly | Where-Object {$_.Status -ne 'OK' -and $_.Status -ne 'Unknown'} | "
              "Select FriendlyName,Class,Status,Problem")
    if not bad:
        rec(PASS, "Device Manager: no devices with problems")
    for d in bad:
        rec(WARN, "Device problem", f"{d.get('FriendlyName')} [{d.get('Class')}] {d.get('Status')} code={d.get('Problem')}")
    allpnp = psj("Get-PnpDevice -PresentOnly | Select FriendlyName,Class")
    names = " | ".join(f"{d.get('FriendlyName')}" for d in allpnp if d.get("FriendlyName")).lower()
    for label, kw in [("Fingerprint reader", "finger"), ("IR/Windows Hello camera", "ir "),
                      ("Webcam", "camera"), ("Smart card reader", "smart card"),
                      ("Bluetooth", "bluetooth"), ("Thunderbolt", "thunderbolt"),
                      ("Touchpad", "touchpad"), ("Wi-Fi", "wi-fi"),
                      ("Ethernet", "ethernet"), ("Audio", "audio")]:
        rec(PASS if kw in names else WARN, f"{label} device present", "" if kw in names else "not found (may be absent on this config)")


def t_cpu():
    c = psj("Get-CimInstance Win32_Processor")
    for p in c:
        rec(INFO, "CPU", f"{p.get('Name')} | {p.get('NumberOfCores')}C/{p.get('NumberOfLogicalProcessors')}T "
                         f"| max {p.get('MaxClockSpeed')} MHz")
        if "AuthenticAMD" in str(p.get("Manufacturer")):
            pass
    st = (c or [{}])[0].get("Status")
    rec(PASS if st == "OK" else WARN, "CPU status", st)
    nm = str((c or [{}])[0].get("Name", ""))
    if not re.search(r"i[579]-1[234]\d+HX|Ultra|Xeon", nm):
        rec(WARN, "CPU is not a typical Fury 16 CPU (HX / Xeon / Ultra)", nm)
    # integrity check: deterministic compute across all cores
    n = mp.cpu_count()
    with mp.Pool(n) as pool:
        res = pool.map(_det_work, [250000] * n * 2)
    rec(PASS if len(set(res)) == 1 else FAIL, "Deterministic compute check on all threads",
        "all results identical" if len(set(res)) == 1 else "MISMATCH - possible unstable CPU/RAM")


def _det_work(n):
    import hashlib
    h = b"x"
    for _ in range(n // 100):
        h = hashlib.sha256(h * 50).digest()
    return h.hex()


def _burn(deadline):
    x = 1.0001
    while time.time() < deadline:
        for _ in range(50000):
            x = (x * 1.0000001) % 1000.0 + 0.5


def _perf_sample():
    out = ps("(Get-Counter '\\Processor Information(_Total)\\% Processor Performance',"
             "'\\Processor Information(_Total)\\% Processor Utility').CounterSamples | % {$_.CookedValue}", 15)
    try:
        v = [float(x.replace(",", ".")) for x in out.split()]
        return v
    except Exception:
        return []


def _acpi_temp():
    out = ps("(Get-CimInstance -Namespace root/wmi MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue "
             "| % {($_.CurrentTemperature/10)-273.15}) -join ' '", 15)
    try:
        return max(float(x.replace(",", ".")) for x in out.split())
    except Exception:
        return None


def t_stress():
    secs = 60 if ARGS.quick else ARGS.stress_seconds
    if shutil.which("stress-ng"):
        rec(INFO, "Using stress-ng")
        procs = [subprocess.Popen(["stress-ng", "--cpu", "0", "--cpu-method", "matrixprod", "-t", f"{secs}s"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    else:
        deadline = time.time() + secs
        procs = [mp.Process(target=_burn, args=(deadline,)) for _ in range(mp.cpu_count())]
        for p in procs:
            p.start()
        rec(INFO, "Using built-in Python burn (lighter than Prime95/OCCT - use those for a harder test)")
    rec(INFO, f"CPU stress for {secs}s - listen for fan, watch for freeze/reboot")
    perfs, temps = [], []
    t0 = time.time()
    while time.time() - t0 < secs:
        v = _perf_sample()
        if v:
            perfs.append(v[0])
        t = _acpi_temp()
        if t:
            temps.append(t)
        print(f"    t={int(time.time() - t0):3d}s perf={v[0]:.0f}%" if v else f"    t={int(time.time() - t0)}s", end="\r")
    print()
    for p in procs:
        try:
            p.join(5) if isinstance(p, mp.Process) else p.wait(10)
        except Exception:
            pass
    if perfs:
        first, last = perfs[:max(1, len(perfs) // 5)], perfs[-max(1, len(perfs) // 5):]
        a, b = sum(first) / len(first), sum(last) / len(last)
        drop = (a - b) / a * 100 if a else 0
        rec(INFO, "Processor performance %", f"start {a:.0f} -> end {b:.0f}  (min {min(perfs):.0f})")
        rec(PASS if drop < 15 else WARN if drop < 35 else FAIL, "Sustained clock drop", f"{drop:.0f}%  (high = thermal throttling: dried paste/dusty fans)")
    else:
        rec(WARN, "Could not read CPU performance counters")
    if temps:
        rec(PASS if max(temps) < 95 else WARN, "ACPI thermal zone max", f"{max(temps):.0f} C (HX CPUs routinely reach 90-100 C under load)")
    else:
        rec(INFO, "No ACPI temp exposed by Windows; verify temps with HWiNFO64 (CPU Package should stay <100 C, no PROCHOT)")
    rec(PASS, "System survived stress without crash/shutdown")


def t_ram():
    mods = psj("Get-CimInstance Win32_PhysicalMemory")
    total = 0
    types = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5"}
    for m in mods:
        cap = int(m.get("Capacity", 0)) / 2**30
        total += cap
        t = types.get(m.get("SMBIOSMemoryType"), m.get("SMBIOSMemoryType"))
        rec(INFO, f"DIMM {m.get('DeviceLocator')}", f"{cap:.0f} GB {t} {m.get('Speed')} MT/s {m.get('Manufacturer')} {m.get('PartNumber', '').strip()}")
    rec(INFO, "Installed RAM", f"{total:.0f} GB in {len(mods)} module(s)")
    if len(mods) == 1:
        rec(WARN, "Single DIMM = single channel (slower); Fury 16 has 4 slots")
    speeds = {m.get("Speed") for m in mods}
    if len(speeds) > 1:
        rec(WARN, "Mixed DIMM speeds", speeds)
    # software pattern test (coarse; MemTest86 is the real test)
    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("ml", ctypes.c_ulong)] + [(f"f{i}", ctypes.c_ulonglong) for i in range(7)]
    ms = MS(); ms.l = ctypes.sizeof(ms)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    avail = ms.f1  # ullAvailPhys
    size = int(min(avail * 0.5, (1 if ARGS.quick else 4) * 2**30))
    bad = 0
    t0 = time.time()
    for pat in (0x00, 0xFF, 0x55, 0xAA, 0x0F, 0xF0):
        buf = bytearray([pat]) * size
        if buf.count(pat) != size:
            bad += 1
        del buf
    # address-in-data test
    chunk = 64 * 2**20
    buf = bytearray(os.urandom(chunk))
    ref = bytes(buf)
    for _ in range(4):
        if bytes(buf) != ref:
            bad += 1
    rec(PASS if not bad else FAIL, f"Software RAM pattern test ({size / 2**30:.1f} GiB, {time.time() - t0:.0f}s)",
        "no errors" if not bad else f"{bad} errors")
    w = psj("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WHEA-Logger'} "
            "-MaxEvents 20 -ErrorAction SilentlyContinue | Select TimeCreated,Id,Message")
    rec(PASS if not w else WARN, "WHEA hardware error events (RAM/CPU/PCIe)", f"{len(w)} found" if w else "none")
    rec(INFO, "MANUAL: run MemTest86 (bootable USB) for >=2 passes on a used workstation - this is the only thorough RAM test")


def t_storage():
    disks = psj("Get-PhysicalDisk | Select DeviceId,FriendlyName,MediaType,BusType,Size,HealthStatus,OperationalStatus,SerialNumber")
    for d in disks:
        did = d.get("DeviceId")
        rec(INFO, f"Disk {did}", f"{d.get('FriendlyName')} {int(d.get('Size', 0)) / 1e9:.0f} GB {d.get('BusType')} {d.get('MediaType')}")
        rec(PASS if d.get("HealthStatus") == "Healthy" else FAIL, f"Disk {did} health", d.get("HealthStatus"))
        rc = (psj(f"Get-PhysicalDisk -DeviceNumber {did} | Get-StorageReliabilityCounter "
                  "| Select Wear,Temperature,PowerOnHours,ReadErrorsTotal,WriteErrorsTotal,ReadErrorsUncorrected,"
                  "WriteErrorsUncorrected,StartStopCycleCount") or [{}])[0]
        if rc:
            rec(INFO, f"Disk {did} counters", rc)
            wear = rc.get("Wear")
            if wear is not None:
                rec(PASS if wear < 50 else WARN if wear < 80 else FAIL, f"Disk {did} wear (% used)", wear)
            poh = rc.get("PowerOnHours")
            if poh is not None:
                rec(PASS if poh < 15000 else WARN, f"Disk {did} power-on hours", poh)
            errs = sum(int(rc.get(k) or 0) for k in ("ReadErrorsUncorrected", "WriteErrorsUncorrected"))
            rec(PASS if errs == 0 else FAIL, f"Disk {did} uncorrected errors", errs)
    if shutil.which("smartctl"):
        for i in range(len(disks)):
            rc, out = run(["smartctl", "-H", "-A", f"/dev/pd{i}"])
            rec(PASS if "PASSED" in out or "OK" in out else WARN, f"smartctl /dev/pd{i}", out.strip().splitlines()[-1] if out else "")
    # speed test
    d = tempfile.mkdtemp(prefix="zbtest_")
    mb = 512 if ARGS.quick else 2048
    blk = os.urandom(4 * 2**20)
    p = os.path.join(d, "bench.bin")
    t0 = time.time()
    with open(p, "wb", buffering=0) as f:
        for _ in range(mb // 4):
            f.write(blk)
        os.fsync(f.fileno())
    w = mb / (time.time() - t0)
    t0 = time.time()
    with open(p, "rb", buffering=0) as f:
        while f.read(4 * 2**20):
            pass
    r = mb / (time.time() - t0)
    # integrity
    ok = open(p, "rb").read(4 * 2**20) == blk
    shutil.rmtree(d, ignore_errors=True)
    rec(INFO, "Sequential speed (system drive, read is cache-assisted)", f"write {w:.0f} MB/s, read {r:.0f} MB/s")
    rec(PASS if w > 800 else WARN if w > 300 else FAIL, "Write speed (NVMe should be >1000 MB/s)", f"{w:.0f} MB/s")
    rec(PASS if ok else FAIL, "Write/read-back data integrity")
    ev = psj("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='disk','Microsoft-Windows-Ntfs','stornvme';Level=1,2} "
             "-MaxEvents 30 -ErrorAction SilentlyContinue | Select TimeCreated,Id")
    rec(PASS if not ev else WARN, "Disk/NTFS error events in System log", f"{len(ev)} found" if ev else "none")
    for v in psj("Get-Volume | Where DriveLetter | Select DriveLetter,FileSystem,SizeRemaining,Size,HealthStatus"):
        rec(INFO, f"Volume {v.get('DriveLetter')}:", f"{v.get('FileSystem')} {int(v.get('SizeRemaining') or 0) / 2**30:.0f}/{int(v.get('Size') or 0) / 2**30:.0f} GB free {v.get('HealthStatus')}")
    rec(INFO, "MANUAL: Fury 16 has up to 4 M.2 slots - check all populated slots appear above")


def t_gpu():
    vc = psj("Get-CimInstance Win32_VideoController | Select Name,DriverVersion,Status,AdapterRAM,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate")
    for v in vc:
        rec(INFO, "GPU", f"{v.get('Name')} drv {v.get('DriverVersion')} status={v.get('Status')} "
                         f"{v.get('CurrentHorizontalResolution')}x{v.get('CurrentVerticalResolution')}@{v.get('CurrentRefreshRate')}Hz")
        rec(PASS if v.get("Status") == "OK" else FAIL, f"{v.get('Name')} status", v.get("Status"))
    names = " ".join(str(v.get("Name")) for v in vc).lower()
    if "nvidia" not in names:
        rec(WARN, "No NVIDIA GPU listed (Fury 16 normally has RTX A-series/RTX Ada) - check driver or hybrid mode")
    if shutil.which("nvidia-smi"):
        q = "name,driver_version,memory.total,temperature.gpu,power.draw,power.limit,clocks.sm,pcie.link.gen.current,pcie.link.width.current"
        rc, out = run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader"])
        rec(INFO if rc == 0 else WARN, "nvidia-smi", out.strip())
        rc, out = run(["nvidia-smi", "-q", "-d", "ECC,PAGE_RETIREMENT"])
        if "Pending" in out and re.search(r"Pending\s*:\s*Yes", out):
            rec(FAIL, "GPU has pending page retirement (memory fault)")
        if torch_ok():
            gpu_stress()
        else:
            rec(INFO, "For GPU stress install PyTorch (CUDA) or run FurMark/OCCT; watch for artifacts/crash/temps >85 C")
    else:
        rec(WARN, "nvidia-smi not found - install NVIDIA driver for deeper GPU checks")
    tdr = psj("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Display','nvlddmkm'} -MaxEvents 20 -ErrorAction SilentlyContinue | Select TimeCreated,Id")
    rec(PASS if not tdr else WARN, "GPU driver reset/TDR events", f"{len(tdr)} found" if tdr else "none")
    rec(INFO, "MANUAL: run FurMark 10 min or 3DMark - look for artifacts (colored dots/lines), driver crash, temp >87 C")


def torch_ok():
    try:
        import torch  # noqa
        return torch.cuda.is_available()
    except Exception:
        return False


def gpu_stress():
    import torch
    secs = 20 if ARGS.quick else 90
    a = torch.randn(8192, 8192, device="cuda", dtype=torch.float16)
    ref = (a @ a)[:64, :64].clone()
    t0, bad, n = time.time(), 0, 0
    while time.time() - t0 < secs:
        c = a @ a
        torch.cuda.synchronize()
        if not torch.equal(c[:64, :64], ref):
            bad += 1
        n += 1
    rec(PASS if not bad else FAIL, f"GPU CUDA matmul stress {secs}s ({n} iterations)", "results consistent" if not bad else f"{bad} mismatches = VRAM/GPU fault")
    rc, out = run(["nvidia-smi", "--query-gpu=temperature.gpu,power.draw", "--format=csv,noheader"])
    rec(INFO, "GPU temp/power after load", out.strip())


class SPS(ctypes.Structure):
    _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte), ("pct", ctypes.c_ubyte), ("sf", ctypes.c_ubyte),
                ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]


def power_status():
    s = SPS()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s))
    return s.ac, s.pct, s.flag


def t_battery():
    d = (psj("Get-CimInstance -Namespace root/wmi BatteryStaticData") or [{}])[0]
    f = (psj("Get-CimInstance -Namespace root/wmi BatteryFullChargedCapacity") or [{}])[0]
    cyc = (psj("Get-CimInstance -Namespace root/wmi BatteryCycleCount") or [{}])[0]
    design, full = d.get("DesignedCapacity"), f.get("FullChargedCapacity")
    rec(INFO, "Battery", f"{d.get('ManufactureName')} {d.get('DeviceName')} S/N {d.get('SerialNumber')}")
    if design and full:
        h = full / design * 100
        rec(PASS if h >= 80 else WARN if h >= 60 else FAIL, "Battery health", f"{h:.0f}% ({full}/{design} mWh)")
    else:
        rec(WARN, "No capacity data via WMI (run powercfg /batteryreport manually)")
    if cyc.get("CycleCount") is not None:
        c = cyc["CycleCount"]
        rec(PASS if c < 300 else WARN if c < 600 else FAIL, "Cycle count", c)
    else:
        rec(INFO, "Cycle count not exposed; see powercfg /batteryreport")
    rep = os.path.join(tempfile.gettempdir(), "battery-report.html")
    run(["powercfg", "/batteryreport", "/output", rep])
    if os.path.exists(rep):
        rec(INFO, "powercfg battery report", rep)
    ac, pct, flag = power_status()
    rec(INFO, "Power now", f"AC={'on' if ac else 'off'}, charge={pct}%")
    if flag & 128:
        rec(FAIL, "No battery detected")
    if not ARGS.no_interactive:
        if ac:
            pause("UNPLUG the charger")
            time.sleep(4)
            ac, pct, _ = power_status()
            rec(PASS if not ac else FAIL, "Runs on battery when unplugged")
        pause("PLUG the charger back in")
        time.sleep(4)
        ac, pct, _ = power_status()
        rec(PASS if ac else FAIL, "Charger detected (check barrel/USB-C charging works; wiggle test the port)")
        rec(INFO, "Tip: charge rate test - put under CPU load on AC; battery % should not fall (330W adapter on RTX configs)")


# ------------------------------------------------------------- GUI tests
def t_display():
    cs = ctypes.windll.user32
    w, h = cs.GetSystemMetrics(0), cs.GetSystemMetrics(1)
    rec(INFO, "Primary resolution", f"{w}x{h} (Fury 16 panels: 1920x1200, 2560x1600, 3840x2400 - 16:10)")
    if abs(w / h - 1.6) > 0.02:
        rec(WARN, "Not 16:10 (may be scaled/external monitor)")
    mon = psj("Get-CimInstance -Namespace root/wmi WmiMonitorID | Select ManufacturerName,UserFriendlyName,ProductCodeID,YearOfManufacture")
    for m in mon:
        dec = lambda a: "".join(chr(x) for x in (a or []) if x)
        rec(INFO, "Monitor", f"{dec(m.get('ManufacturerName'))} {dec(m.get('UserFriendlyName'))} {dec(m.get('ProductCodeID'))} year {m.get('YearOfManufacture')}")
    if ARGS.no_interactive:
        rec(SKIP, "Dead-pixel colour test", "non-interactive")
        return
    try:
        import tkinter as tk
    except Exception:
        rec(WARN, "tkinter missing, cannot run colour test")
        return
    colors = ["black", "white", "red", "green", "blue", "#808080", "cyan", "magenta", "yellow"]
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    idx = [0]
    root.configure(bg=colors[0], cursor="none")

    def nxt(_e=None):
        idx[0] += 1
        if idx[0] >= len(colors):
            root.destroy()
        else:
            root.configure(bg=colors[idx[0]])
    root.bind("<Button-1>", nxt); root.bind("<space>", nxt); root.bind("<Return>", nxt)
    root.bind("<Escape>", lambda e: root.destroy())
    root.focus_force()
    print("  Fullscreen colours: click / Space for next. Look for dead/stuck pixels, bright spots, backlight bleed, yellow tint, flicker, burn-in/delamination.")
    root.mainloop()
    yn_result("No dead/stuck pixels, bleed or flicker", "Was the screen perfect (no dead pixels, bleed, lines, flicker)?", WARN)
    yn_result("Brightness keys / auto-brightness work", "Do Fn brightness keys change brightness smoothly (no PWM flicker at low levels)?", WARN)
    pause("Open and close the lid slowly; check it wakes/sleeps and hinges are firm")
    yn_result("Hinges firm, lid sleep/wake works, no wobble/cracks", "Hinges and lid OK?")


LAYOUT = [
    ["Escape"] + [f"F{i}" for i in range(1, 13)] + ["Delete"],
    ["grave"] + [str(i) for i in range(1, 10)] + ["0", "minus", "equal", "BackSpace"],
    ["Tab"] + list("qwertyuiop") + ["bracketleft", "bracketright", "backslash"],
    ["Caps_Lock"] + list("asdfghjkl") + ["semicolon", "apostrophe", "Return"],
    ["Shift_L"] + list("zxcvbnm") + ["comma", "period", "slash", "Shift_R"],
    ["Control_L", "Super_L", "Alt_L", "space", "Alt_R", "Control_R", "Left", "Up", "Down", "Right"],
    ["Home", "End", "Prior", "Next", "Insert"],
    ["KP_Divide", "KP_Multiply", "KP_Subtract", "KP_7", "KP_8", "KP_9", "KP_Add", "KP_4", "KP_5", "KP_6",
     "KP_1", "KP_2", "KP_3", "KP_Enter", "KP_0", "KP_Decimal"],
]
# keys whose tk keysym changes with NumLock
KP_ALT = {"KP_7": "KP_Home", "KP_8": "KP_Up", "KP_9": "KP_Prior", "KP_4": "KP_Left", "KP_5": "KP_Begin", "KP_6": "KP_Right",
          "KP_1": "KP_End", "KP_2": "KP_Down", "KP_3": "KP_Next", "KP_0": "KP_Insert", "KP_Decimal": "KP_Delete"}


def t_keyboard():
    if ARGS.no_interactive:
        rec(SKIP, "Keyboard test", "non-interactive")
        return
    try:
        import tkinter as tk
    except Exception:
        rec(WARN, "tkinter missing")
        return
    root = tk.Tk()
    root.title("Keyboard + touchpad test - press every key, click all buttons, then click DONE")
    root.geometry("1100x520")
    labels, pressed, mouse = {}, set(), set()
    for r, row in enumerate(LAYOUT):
        for c, k in enumerate(row):
            lb = tk.Label(root, text=k.replace("KP_", "N").replace("Escape", "Esc"), width=7, relief="raised", bg="#ddd")
            lb.grid(row=r, column=c, padx=1, pady=2)
            labels[k] = lb
    inv = {v: k for k, v in KP_ALT.items()}
    status = tk.Label(root, text="Mouse/touchpad: L R scroll  | Also test Fn keys: volume, mute, mic, brightness, airplane, Fn lock, backlight", font=("Arial", 11))
    status.grid(row=len(LAYOUT) + 1, column=0, columnspan=16, pady=10)

    def kd(e):
        k = inv.get(e.keysym, e.keysym)
        if k == "ISO_Left_Tab":
            k = "Tab"
        if k in labels:
            labels[k].configure(bg="#6f6")
            pressed.add(k)
    root.bind_all("<KeyPress>", kd)

    def mb(e):
        mouse.add({1: "left", 2: "middle", 3: "right"}.get(e.num, str(e.num)))
        status.configure(text=f"Mouse buttons seen: {sorted(mouse)}")

    def wheel(_e):
        mouse.add("scroll")
        status.configure(text=f"Mouse buttons seen: {sorted(mouse)}")
    root.bind_all("<Button>", mb)
    root.bind_all("<MouseWheel>", wheel)
    tk.Button(root, text="DONE", command=root.destroy, font=("Arial", 16)).grid(row=len(LAYOUT) + 2, column=0, columnspan=16, pady=10)
    root.mainloop()
    allk = [k for row in LAYOUT for k in row]
    missing = [k for k in allk if k not in pressed]
    if len(missing) == 0:
        rec(PASS, "All keys registered")
    else:
        rec(FAIL if len(missing) < 15 else WARN, f"{len(missing)} key(s) not registered", ", ".join(missing))
        rec(INFO, "(Numpad keys may not exist/be needed; Fury 16 has a full numpad. Super_L=Windows key)")
    rec(PASS if {"left", "right"} <= mouse else WARN, "Touchpad/mouse buttons", sorted(mouse))
    yn_result("Keyboard backlight works (Fn+F5 or key)", "Does the keyboard backlight turn on and adjust?", WARN)
    yn_result("Touchpad: smooth tracking, multi-touch gestures, no dead zones", "Touchpad moves smoothly everywhere, 2-finger scroll works?")
    yn_result("Pointing stick/other pointing device (if present)", "Is TrackPoint/pointing stick OK (or not fitted)?", WARN)


def t_audio():
    rec(INFO, "Audio devices", "; ".join(str(d.get("Name")) for d in psj("Get-CimInstance Win32_SoundDevice")))
    if ARGS.no_interactive:
        rec(SKIP, "Speaker/mic test", "non-interactive")
        return
    try:
        import winsound
        print("  Playing left/right test tones...")
        for f in (440, 880, 1760):
            winsound.Beep(f, 500)
    except Exception as e:
        rec(WARN, "Could not play tone", e)
    yn_result("Speakers clear on both sides, no rattle/distortion", "Did you hear clear tones (try volume up) from both speakers?")
    # mic via MCI
    wav = os.path.join(tempfile.gettempdir(), "zb_mic.wav")
    mci = ctypes.windll.winmm.mciSendStringW
    pause("MIC test: after Enter, speak/tap near laptop for 3 seconds")
    mci("open new type waveaudio alias zbm", None, 0, 0)
    mci("record zbm", None, 0, 0)
    time.sleep(3)
    mci("stop zbm", None, 0, 0)
    mci(f'save zbm "{wav}"', None, 0, 0)
    mci("close zbm", None, 0, 0)
    try:
        import wave, array
        with wave.open(wav) as w:
            data = array.array("h", w.readframes(w.getnframes()))
        peak = max((abs(x) for x in data), default=0)
        rec(PASS if peak > 1500 else WARN, "Microphone level", f"peak {peak}/32767 (low = muted/broken mic)")
        mci(f'play "{wav}" wait', None, 0, 0)
    except Exception as e:
        rec(WARN, "Mic recording failed", e)
    yn_result("3.5mm headphone jack works", "Plug in headphones: is sound on them, speakers cut off, jack grips firmly? (s to skip)", WARN)


def t_camera():
    cams = psj("Get-PnpDevice -Class Camera,Image -PresentOnly | Select FriendlyName,Status")
    for c in cams:
        rec(PASS if c.get("Status") == "OK" else WARN, f"Camera: {c.get('FriendlyName')}", c.get("Status"))
    if not cams:
        rec(FAIL, "No camera found")
    if not ARGS.no_interactive:
        os.system("start microsoft.windows.camera:")
        yn_result("Webcam image clear, privacy shutter/switch works", "Does the camera app show a clear image (and Fn camera-privacy key works)?")
        yn_result("Windows Hello IR face login works (if present)", "Hello face/fingerprint can be enrolled in Settings > Accounts > Sign-in? (s=skip)", WARN)


def t_network():
    ad = psj("Get-NetAdapter -Physical | Select Name,InterfaceDescription,Status,LinkSpeed,MacAddress")
    for a in ad:
        rec(INFO, f"Adapter {a.get('Name')}", f"{a.get('InterfaceDescription')} {a.get('Status')} {a.get('LinkSpeed')}")
    wifi = [a for a in ad if re.search("wi-?fi|wireless|802.11", str(a.get("InterfaceDescription")) + str(a.get("Name")), re.I)]
    rec(PASS if wifi else FAIL, "Wi-Fi adapter present")
    if wifi:
        rc, out = run(["netsh", "wlan", "show", "networks", "mode=bssid"], 30)
        n = len(re.findall(r"^SSID \d+", out, re.M))
        rec(PASS if n else WARN, "Wi-Fi scan", f"{n} networks seen")
        for b in ("2.4", "5", "6"):
            pass
        bands = set(re.findall(r"Band\s*:\s*([\d.]+)", out))
        rec(INFO, "Bands seen", ", ".join(sorted(bands)))
    eth = [a for a in ad if re.search(r"ethernet|gbe|2\.5|i219|i225|i226", str(a.get("InterfaceDescription")), re.I)]
    rec(PASS if eth else WARN, "Ethernet adapter present")
    bt = psj("Get-PnpDevice -Class Bluetooth -PresentOnly | Select FriendlyName,Status")
    rec(PASS if any("radio" in str(b.get("FriendlyName")).lower() or "intel" in str(b.get("FriendlyName")).lower() for b in bt) else WARN,
        "Bluetooth radio", f"{len(bt)} devices")
    rc, out = run(["ping", "-n", "4", "1.1.1.1"], 30)
    m = re.search(r"Average = (\d+)ms", out)
    rec(PASS if m else WARN, "Internet ping", f"{m.group(1)} ms" if m else "failed (offline?)")
    if not ARGS.no_interactive:
        yn_result("Wi-Fi signal/speed fine near router; Bluetooth pairs with a device", "Connect to Wi-Fi + pair a Bluetooth device: OK? (s=skip)", WARN)
        yn_result("Ethernet RJ45 link works (plug a cable)", "Does a cable give link/internet? (s=skip)", WARN)


def snapshot():
    usb = {d.get("InstanceId") for d in psj("Get-PnpDevice -PresentOnly -Class USB,HIDClass,DiskDrive,USBDevice,WPD,Net | Select InstanceId")}
    disks = {d.get("Number") for d in psj("Get-Disk | Select Number")}
    mons = len(psj("Get-CimInstance -Namespace root/wmi WmiMonitorID | Select InstanceName"))
    return usb, disks, mons


def t_ports():
    if ARGS.no_interactive:
        rec(SKIP, "Port tests", "non-interactive")
        return
    ports = ["USB-A left side", "USB-A right side", "USB-C / Thunderbolt 4 #1", "USB-C / Thunderbolt 4 #2",
             "HDMI 2.1 (connect monitor/TV)", "SD card reader (insert card)", "Smart card reader", "RJ45 (cable)",
             "Charging port: DC barrel / USB-C"]
    print("  For each port: plug a device when asked, wait for detection, then remove it. 's' to skip a port.")
    for p in ports:
        a = input(f"  > Test '{p}'? [Enter=yes / s=skip]: ").strip().lower()
        if a.startswith("s"):
            rec(SKIP, p)
            continue
        base = snapshot()
        print(f"    Plug into: {p}  (waiting 25 s)")
        ok = False
        for _ in range(12):
            time.sleep(2)
            cur = snapshot()
            if cur[0] - base[0] or cur[1] - base[1] or cur[2] > base[2]:
                ok = True
                break
        if ok:
            rec(PASS, f"Port: {p}", "device detected")
        else:
            r = ask_yn(f"Nothing auto-detected on {p}. Did it actually work (e.g. device lit up / sound / network)?")
            rec(PASS if r else (SKIP if r is None else FAIL), f"Port: {p}", "manual confirm")
        input("    Remove device, check connector is snug / not loose, Enter... ")
    rec(INFO, "Test USB-C/TB ports with a Thunderbolt/USB-C display or dock (video + data + charging) if possible")


def t_events():
    d = 90
    kp = psj(f"Get-WinEvent -FilterHashtable @{{LogName='System';Id=41;StartTime=(Get-Date).AddDays(-{d})}} -ErrorAction SilentlyContinue | Select TimeCreated")
    bc = psj(f"Get-WinEvent -FilterHashtable @{{LogName='System';Id=1001;ProviderName='Microsoft-Windows-WER-SystemErrorReporting';StartTime=(Get-Date).AddDays(-{d})}} -ErrorAction SilentlyContinue | Select TimeCreated")
    th = psj(f"Get-WinEvent -FilterHashtable @{{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=37;StartTime=(Get-Date).AddDays(-{d})}} -ErrorAction SilentlyContinue | Select TimeCreated")
    rec(PASS if len(kp) < 3 else WARN, f"Unexpected shutdowns (Kernel-Power 41), last {d}d", len(kp))
    rec(PASS if not bc else WARN, f"Blue screens, last {d}d", len(bc))
    rec(PASS if len(th) < 5 else WARN, "Processor speed-limited events (thermal/power throttling)", len(th))
    first = ps("(Get-CimInstance Win32_OperatingSystem).InstallDate")
    rec(INFO, "Windows install date", first)


def t_checklist():
    if ARGS.no_interactive:
        rec(SKIP, "Physical inspection", "non-interactive")
        return
    items = [
        "Chassis: no cracks, dents, bent corners (aluminum body), no missing rubber feet",
        "Bottom screws: all present, none stripped/chewed (sign of prior opening)",
        "Screen: no pressure marks, delamination, cracks, keyboard imprints on the panel",
        "Hinges: no play, no cracked hinge covers around the display base",
        "Keyboard keycaps: none missing, no heavy shine / liquid residue under keys",
        "Palm rest / touchpad: no lifting, battery not swollen (laptop sits flat, trackpad not raised)",
        "Ports: no bent pins, loose USB-C, burned/blackened contacts, charger pin not wobbly",
        "Vents/heatsinks: blow a light with a torch in vents - heavy dust/clogging? Fans loud or grinding?",
        "Original HP charger (230W/330W) included; labels (service tag) not tampered",
        "Smell: no burning/liquid-damage smell; no corrosion visible in USB/headphone jack",
        "Speakers/mic grills: not clogged; no rattle when tapped",
        "Fan noise: ramps up and down smoothly under load (stress test), no ticking/grinding",
        "Webcam privacy shutter slides, SD/smart card slot flap OK",
    ]
    for it in items:
        yn_result(it.split(":")[0], it + " -> OK?", WARN)


# ------------------------------------------------------------------- main
TESTS = [
    ("sysinfo", "System identity, security & corporate-lock checks", t_sysinfo, False),
    ("devices", "Device Manager & expected components", t_devices, False),
    ("events", "Crash / shutdown history", t_events, False),
    ("cpu", "CPU identity & integrity", t_cpu, False),
    ("ram", "RAM", t_ram, False),
    ("storage", "Storage health & speed", t_storage, False),
    ("battery", "Battery & charger", t_battery, True),
    ("gpu", "GPU", t_gpu, False),
    ("stress", "CPU stress / thermal throttling", t_stress, False),
    ("display", "Display & hinges", t_display, True),
    ("keyboard", "Keyboard / touchpad", t_keyboard, True),
    ("audio", "Audio", t_audio, True),
    ("camera", "Camera & biometrics", t_camera, True),
    ("network", "Wi-Fi / Bluetooth / Ethernet", t_network, False),
    ("ports", "Ports", t_ports, True),
    ("checklist", "Physical inspection", t_checklist, True),
]


def save_report():
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desk):
        desk = os.getcwd()
    serial = next((d for s, st, n, d in RESULTS if n == "Serial"), "unknown")
    base = os.path.join(desk, f"zbook_report_{re.sub(r'[^A-Za-z0-9]', '', serial)}_{datetime.datetime.now():%Y%m%d_%H%M}")
    lines = []
    for sec, st, n, d in RESULTS:
        lines.append(f"[{st}] {sec}: {n}" + (f" - {d}" if d else ""))
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump([dict(section=s, status=st, name=n, detail=d) for s, st, n, d in RESULTS], f, indent=2)
    return base


def main():
    global ARGS
    mp.freeze_support()   # must run before argparse so multiprocessing children work in a frozen exe
    ap = argparse.ArgumentParser(description="HP ZBook Fury 16 used-laptop tester (Windows)")
    ap.add_argument("--only", default="", help="comma list of test keys")
    ap.add_argument("--skip", default="", help="comma list of test keys to skip")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-interactive", action="store_true")
    ap.add_argument("--stress-seconds", type=int, default=300)
    ap.add_argument("--list", action="store_true")
    ARGS = ap.parse_args()
    if ARGS.list:
        for k, t, _, i in TESTS:
            print(f"{k:10} {t}{'  (interactive)' if i else ''}")
        return
    if os.name != "nt":
        sys.exit("This script targets Windows. Run it on the laptop under Windows.")
    enable_ansi()
    mp.freeze_support()
    if not is_admin():
        print("WARNING: not running as Administrator - TPM, BitLocker, SMART counters, event logs may be incomplete.\n")
    only = {x for x in ARGS.only.split(",") if x}
    skip = {x for x in ARGS.skip.split(",") if x}
    for key, title, fn, _ in TESTS:
        if (only and key not in only) or key in skip:
            continue
        CURRENT[0] = key
        print(f"\n=== {title} ===")
        try:
            fn()
        except KeyboardInterrupt:
            rec(SKIP, title, "interrupted")
        except Exception as e:
            rec(WARN, f"{title}: test crashed", repr(e))
    cnt = {s: sum(1 for r in RESULTS if r[1] == s) for s in (PASS, WARN, FAIL, SKIP)}
    print("\n" + "=" * 60)
    print(f"SUMMARY: {cnt[PASS]} pass, {cnt[WARN]} warn, {cnt[FAIL]} FAIL, {cnt[SKIP]} skipped")
    for sec, st, n, d in RESULTS:
        if st in (FAIL, WARN):
            print(f"  {COLORS[st]}[{st}]{RESET} {sec}: {n} {('- ' + d) if d else ''}")
    verdict = "DO NOT BUY / negotiate hard" if cnt[FAIL] else ("Buy only with price discount for warnings" if cnt[WARN] > 5 else "Looks good")
    print(f"\nVerdict: {verdict}")
    print("Report saved:", save_report() + ".txt/.json")


if __name__ == "__main__":
    main()
