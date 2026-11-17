// MCPBRIDGE.DLL - Proteus VSM model that exposes a live simulation to an external
// controller (our MCP server) over ONE Windows named pipe per Proteus process.
//
// Attach to any part:  MODDLL=MCPBRIDGE.DLL  with
//   PRIMITIVE=DIGITAL,MCPBRIDGE_D -> digital probe (pin logic states, SET/HOLD/STEP control)
//   PRIMITIVE=ANALOG,MCPBRIDGE_A  -> analog probe  (pin voltages, read-only)
// (the model name after the comma selects the kind: only MCPBRIDGE_A is analog)
// A circuit may contain several probes; they all share the same pipe.
// Optional part properties:
//   BRIDGEPINS = comma list of pin names to expose (default: try common names, "1".."32")
//   PIPE       = pipe name (default proteus_mcp), first probe in the process wins
//   TICK       = digital service period in seconds of simulation time (default 0.001)
//   START      = RUN or HOLD (digital only; HOLD pauses at t=0 until a client sends RUN/STEP)
//
// Protocol: one ASCII line per request, one line per reply ("OK ..." or "ERR ...").
//   PING                     -> OK pong
//   TIME                     -> OK <seconds>           (digital time, else analog time)
//   PINS                     -> OK A,B,Q               (digital pins)
//   READ                     -> OK t=<s> A=1 B=0 Q=Z   (digital: 0 1 Z X)
//   SET <pin> <0|1|Z>        -> OK            (driven at the next service point)
//   EVENTS                   -> OK <t>:<pin>=<v>;...   (edges since last call)
//   HOLD                     -> OK held t=<s>
//   STEP <seconds>           -> OK held t=<s> (runs then holds; replies when target reached)
//   RUN                      -> OK running
//   APINS                    -> OK IN,OUT              (analog pins)
//   AREAD                    -> OK t=<s> IN=5 OUT=2.5  (volts, %.9g, latest accepted timepoint)
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string>
#include <vector>
#include <deque>
#include <algorithm>
#include "vsm.hpp"

static const ABSTIME TICKS_PER_SEC = 1000000000000LL; // DSIMTICK = 1e-12

// Diagnostic trace: %TEMP%\mcpbridge.log
static void trace(const char *fmt, ...) {
    char path[MAX_PATH]; GetTempPathA(MAX_PATH, path); strcat_s(path, "mcpbridge.log");
    FILE *f = 0; if (fopen_s(&f, path, "a") || !f) return;
    va_list ap; va_start(ap, fmt); vfprintf(f, fmt, ap); va_end(ap);
    fputc('\n', f); fclose(f);
}

static char stateChar(STATE s) {
    if (ishigh(s)) return '1';
    if (islow(s)) return '0';
    if (isfloating(s)) return 'Z';
    return 'X';
}

static std::string fmtSeconds(double t) { char b[48]; sprintf_s(b, "%.12g", t); return b; }

// Pin names to try when BRIDGEPINS is not given.
static std::vector<std::string> candidatePins(IINSTANCE *inst) {
    std::vector<std::string> out;
    CHAR *list = inst->getstrval((CHAR *)"BRIDGEPINS", (CHAR *)"");
    std::string s = list ? list : "";
    if (s.empty()) {
        const char *defs[] = {"A","B","C","D","Q","IN","OUT","CLK","EN","RST","TX","RX","SDA","SCL",
                              "D0","D1","D2","D3","D4","D5","D6","D7",
                              "D8","D9","D10","D11","D12","D13","D14","D15"};
        for (auto d : defs) out.push_back(d);
        for (int i = 1; i <= 32; i++) out.push_back(std::to_string(i));
    } else {
        size_t p = 0;
        while (p <= s.size()) {
            size_t e = s.find(',', p); if (e == std::string::npos) e = s.size();
            std::string n = s.substr(p, e - p);
            size_t b = n.find_first_not_of(" \t"), z = n.find_last_not_of(" \t");
            if (b != std::string::npos) out.push_back(n.substr(b, z - b + 1));
            p = e + 1;
        }
    }
    return out;
}

static std::string pipeNameFor(IINSTANCE *inst) {
    CHAR *pn = inst->getstrval((CHAR *)"PIPE", (CHAR *)"proteus_mcp");
    return std::string("\\\\.\\pipe\\") + (pn && *pn ? pn : "proteus_mcp");
}

class Bridge;
class AnalogProbe;

// One pipe server per process, shared by every probe instance (global registry).
class Server {
public:
    static Server &get() { static Server s; return s; }

    void add(Bridge *b, const std::string &pipe) { EnterCriticalSection(&lk); digs.push_back(b); start(pipe); LeaveCriticalSection(&lk); }
    void add(AnalogProbe *a, const std::string &pipe) { EnterCriticalSection(&lk); ans.push_back(a); start(pipe); LeaveCriticalSection(&lk); }
    void remove(Bridge *b) {
        EnterCriticalSection(&lk);
        digs.erase(std::remove(digs.begin(), digs.end(), b), digs.end());
        bool idle = digs.empty() && ans.empty(); LeaveCriticalSection(&lk);
        if (idle) stop();
    }
    void remove(AnalogProbe *a) {
        EnterCriticalSection(&lk);
        ans.erase(std::remove(ans.begin(), ans.end(), a), ans.end());
        bool idle = digs.empty() && ans.empty(); LeaveCriticalSection(&lk);
        if (idle) stop();
    }
    std::string handle(const std::string &line);

private:
    CRITICAL_SECTION lk; std::vector<Bridge *> digs; std::vector<AnalogProbe *> ans;
    HANDLE thread = 0; volatile bool running = false; std::string name;
    Server() { InitializeCriticalSection(&lk); }

    void start(const std::string &pipe) {            // lk held
        if (thread) return;
        name = pipe; running = true;
        thread = CreateThread(0, 0, main, this, 0, 0);
        trace("server started on %s", name.c_str());
    }
    void stop() {
        EnterCriticalSection(&lk);
        HANDLE t = thread; thread = 0; running = false;
        LeaveCriticalSection(&lk);
        if (!t) return;
        for (int i = 0; i < 40 && WaitForSingleObject(t, 50) != WAIT_OBJECT_0; i++) {
            CancelSynchronousIo(t);                  // unblock ConnectNamedPipe/ReadFile
            HANDLE h = CreateFileA(name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, 0, OPEN_EXISTING, 0, 0);
            if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
        }
        CloseHandle(t);
        trace("server stopped");
    }
    static DWORD WINAPI main(LPVOID p) { ((Server *)p)->loop(); return 0; }
    void loop();
};

// ---------------------------------------------------------------- analog probe
class AnalogProbe : public ISPICEMODEL {
public:
    explicit AnalogProbe(bool on) : enabled(on), inst(0), t(0), valid(false) { InitializeCriticalSection(&lock); }
    ~AnalogProbe() { Server::get().remove(this); DeleteCriticalSection(&lock); }

    INT isanalog(CHAR *) { return enabled; }

    VOID setup(IINSTANCE *instance, ISPICECKT *) {
        if (!enabled) return;
        inst = instance;
        id = inst->id() ? inst->id() : "";
        trace("analog setup %s PRIMITIVE=%s", id.c_str(), inst->getstrval((CHAR *)"PRIMITIVE", (CHAR *)"?"));
        for (auto &n : candidatePins(inst)) {
            SPICENODE node = inst->getspicenode((CHAR *)n.c_str(), FALSE);
            trace("analog %s pin %s -> node %d", id.c_str(), n.c_str(), (int)node);
            if (node >= 0) { names.push_back(n); nodes.push_back(node); }
        }
        volts.assign(names.size(), 0.0);
        inst->log((CHAR *)"MCP analog probe: %d pins", (int)names.size());
        // Proteus builds both model kinds for a part; only register if it has analog pins.
        if (!names.empty()) Server::get().add(this, pipeNameFor(inst));
    }
    VOID runctrl(RUNMODES mode) { if (mode == RM_STOP) Server::get().remove(this); }
    VOID actuate(REALTIME, ACTIVESTATE) {}
    BOOL indicate(REALTIME, ACTIVEDATA *) { return FALSE; }
    VOID dcload(REALTIME time, SPICEMODES, DOUBLE *oldrhs, DOUBLE *) { if (!valid) snapshot(time, oldrhs); }
    VOID acload(SPICEFREQ, DOUBLE *, DOUBLE *) {}
    VOID trunc(REALTIME, REALTIME *) {}
    VOID accept(REALTIME time, DOUBLE *rhs) { snapshot(time, rhs); valid = true; }

    // Called from the pipe thread.
    std::string pinList(bool prefix) {
        std::string r;
        for (size_t i = 0; i < names.size(); i++) r += (i ? "," : "") + label(i, prefix);
        return r;
    }
    std::string values(bool prefix) {
        EnterCriticalSection(&lock);
        std::string r;
        for (size_t i = 0; i < names.size(); i++) {
            char b[48]; sprintf_s(b, "%.9g", volts[i]);
            r += " " + label(i, prefix) + "=" + b;
        }
        LeaveCriticalSection(&lock);
        return r;
    }
    double time() { EnterCriticalSection(&lock); double r = t; LeaveCriticalSection(&lock); return r; }
    bool hasData() { return valid; }

private:
    IINSTANCE *inst; std::string id;
    std::vector<std::string> names; std::vector<SPICENODE> nodes; std::vector<double> volts;
    bool enabled; CRITICAL_SECTION lock; double t; volatile bool valid;

    std::string label(size_t i, bool prefix) { return prefix ? id + "." + names[i] : names[i]; }
    void snapshot(REALTIME time, DOUBLE *rhs) {
        EnterCriticalSection(&lock);
        t = time;
        for (size_t i = 0; i < nodes.size(); i++) volts[i] = nodes[i] > 0 ? rhs[nodes[i]] : 0.0;
        LeaveCriticalSection(&lock);
    }
};

// ---------------------------------------------------------------- digital probe
class Bridge : public IDSIMMODEL {
public:
    explicit Bridge(bool on) : enabled(on), inst(0), ckt(0), now(0), holdUntil(-1), held(false), running(false), inUse(0),
               reqEvent(CreateEvent(0, FALSE, FALSE, 0)),
               repEvent(CreateEvent(0, FALSE, FALSE, 0)) {
        InitializeCriticalSection(&lock);
    }
    ~Bridge() {
        shutdown();
        CloseHandle(reqEvent); CloseHandle(repEvent);
        DeleteCriticalSection(&lock);
    }

    INT isdigital(CHAR *) { return enabled; }

    VOID setup(IINSTANCE *instance, IDSIMCKT *dsim) {
        if (!enabled) return;
        inst = instance; ckt = dsim;
        trace("digital setup %s PRIMITIVE=%s", inst->id(), inst->getstrval((CHAR *)"PRIMITIVE", (CHAR *)"?"));
        for (auto &n : candidatePins(inst)) {
            IDSIMPIN *pin = inst->getdsimpin((CHAR *)n.c_str(), FALSE);
            trace("digital %s pin %s -> %p", inst->id(), n.c_str(), (void *)pin);
            if (pin) { names.push_back(n); pins.push_back(pin); last.push_back(pin->istate()); }
        }
        double tick = 0.001;
        inst->getnumval(&tick, (CHAR *)"TICK", 0.001);
        if (tick < 1e-9) tick = 1e-9;
        tickTicks = (ABSTIME)(tick * (double)TICKS_PER_SEC);
        CHAR *start = inst->getstrval((CHAR *)"START", (CHAR *)"RUN");
        if (start && _stricmp(start, "HOLD") == 0) holdUntil = 0;
        std::string pipe = pipeNameFor(inst);
        inst->log((CHAR *)"MCP bridge: %d pins on %s", (int)pins.size(), pipe.c_str());
        trace("setup: %d pins on %s", (int)pins.size(), pipe.c_str());
        if (pins.empty()) return;            // e.g. an ANALOG-only part: not a digital probe
        running = true;
        Server::get().add(this, pipe);
        ckt->setcallback(0, this, 1);
    }

    VOID runctrl(RUNMODES mode) { if (mode == RM_STOP) shutdown(); }
    VOID actuate(REALTIME, ACTIVESTATE) {}
    BOOL indicate(REALTIME, ACTIVEDATA *) { return FALSE; }

    VOID simulate(ABSTIME time, DSIMMODES) {
        // Record edges on any exposed pin with their exact simulation time.
        EnterCriticalSection(&lock);
        for (size_t i = 0; i < pins.size(); i++) {
            STATE st = pins[i]->istate();
            if (stateChar(st) != stateChar(last[i])) {
                char buf[96];
                sprintf_s(buf, "%.12g:%s=%c", (double)time / TICKS_PER_SEC, names[i].c_str(), stateChar(st));
                events.push_back(buf);
                if (events.size() > 20000) events.pop_front();
                last[i] = st;
            }
        }
        LeaveCriticalSection(&lock);
    }

    VOID callback(ABSTIME time, EVENTID) {
        now = time;
        applyDrives(time);
        serveOnce(time);                     // answer any pending request quickly
        if (holdUntil >= 0 && time >= holdUntil) {
            held = true;
            if (stepPending) {                // STEP replies only once the target is reached
                stepPending = false;
                EnterCriticalSection(&lock); rep = "OK held t=" + fmtTime(time); LeaveCriticalSection(&lock);
                SetEvent(repEvent);
            }
            DWORD idle = GetTickCount();
            // Freeze the simulator thread here and serve requests until released.
            while (running && held) {
                if (WaitForSingleObject(reqEvent, 50) == WAIT_OBJECT_0) {
                    process(time); idle = GetTickCount();
                    applyDrives(time);
                } else if (GetTickCount() - idle > 600000) {   // safety: 10 min idle
                    held = false; holdUntil = -1;
                }
            }
        }
        if (running) ckt->setcallback(time + tickTicks, this, 1);
    }

    // Called from the pipe thread: hand one request to the simulator thread, wait for its reply.
    std::string submit(const std::string &line) {
        if (!running) return "ERR simulation not running";
        InterlockedIncrement(&inUse);
        std::string answer;
        EnterCriticalSection(&lock); req = line; LeaveCriticalSection(&lock);
        ResetEvent(repEvent);
        SetEvent(reqEvent);
        // Answered by the simulator thread at its next service point.
        // STEP may need long wall time to reach its target: allow 5 minutes.
        DWORD limit = _strnicmp(line.c_str(), "STEP", 4) == 0 ? 300000 : 3000, waited = 0;
        bool got = false;
        while (running && waited < limit) {
            if (WaitForSingleObject(repEvent, 50) == WAIT_OBJECT_0) { got = true; break; }
            waited += 50;
        }
        if (got) { EnterCriticalSection(&lock); answer = rep; LeaveCriticalSection(&lock); }
        else { ResetEvent(reqEvent); answer = running ? "ERR simulation not running" : "ERR simulation stopped"; }
        InterlockedDecrement(&inUse);
        return answer;
    }
    std::string pinList() { std::string r; for (size_t i = 0; i < names.size(); i++) r += (i ? "," : "") + names[i]; return r; }

private:
    bool enabled;
    IINSTANCE *inst; IDSIMCKT *ckt;
    std::vector<std::string> names; std::vector<IDSIMPIN *> pins; std::vector<STATE> last;
    std::vector<std::pair<size_t, STATE>> pendingDrives;
    std::deque<std::string> events;
    ABSTIME tickTicks = 1000000000, now, holdUntil; volatile bool held, running; volatile LONG inUse;
    HANDLE reqEvent, repEvent;
    CRITICAL_SECTION lock; std::string req, rep; volatile bool stepPending = false;

    void applyDrives(ABSTIME time) {
        EnterCriticalSection(&lock);
        auto todo = pendingDrives; pendingDrives.clear();
        LeaveCriticalSection(&lock);
        for (auto &d : todo) pins[d.first]->setstate(time, 1, d.second);
    }

    void serveOnce(ABSTIME time) {
        if (WaitForSingleObject(reqEvent, 0) == WAIT_OBJECT_0) process(time);
    }

    std::string fmtTime(ABSTIME t) { return fmtSeconds((double)t / TICKS_PER_SEC); }

    // Runs on the simulator thread: safe to touch pins and time.
    void process(ABSTIME time) {
        std::string r, q;
        EnterCriticalSection(&lock); q = req; LeaveCriticalSection(&lock);
        char cmd[16] = {0}, a1[64] = {0}, a2[16] = {0};
        sscanf_s(q.c_str(), "%15s %63s %15s", cmd, (unsigned)sizeof(cmd), a1, (unsigned)sizeof(a1), a2, (unsigned)sizeof(a2));
        std::string c = cmd;
        if (c == "PING") r = "OK pong";
        else if (c == "TIME") r = "OK " + fmtTime(time);
        else if (c == "PINS") r = "OK " + pinList();
        else if (c == "READ") {
            r = "OK t=" + fmtTime(time);
            for (size_t i = 0; i < pins.size(); i++) { r += " " + names[i] + "="; r += stateChar(pins[i]->istate()); }
        } else if (c == "SET") {
            size_t i = 0; while (i < names.size() && _stricmp(names[i].c_str(), a1) != 0) i++;
            STATE st = a2[0] == '1' ? SHI : a2[0] == '0' ? SLO : (a2[0] == 'Z' || a2[0] == 'z') ? FLT : (STATE)0;
            if (i == names.size()) r = "ERR unknown pin";
            else if (!st && a2[0] != 'Z' && a2[0] != 'z') r = "ERR value must be 0, 1 or Z";
            else { EnterCriticalSection(&lock); pendingDrives.push_back({i, st}); LeaveCriticalSection(&lock); r = "OK"; }
        } else if (c == "EVENTS") {
            EnterCriticalSection(&lock);
            r = "OK "; for (size_t i = 0; i < events.size(); i++) r += (i ? ";" : "") + events[i];
            events.clear();
            LeaveCriticalSection(&lock);
        } else if (c == "HOLD") { holdUntil = time; held = true; r = "OK held t=" + fmtTime(time); }
        else if (c == "STEP") {
            double sec = atof(a1);
            if (sec <= 0) r = "ERR STEP needs seconds > 0";
            else { holdUntil = time + (ABSTIME)(sec * (double)TICKS_PER_SEC); held = false; stepPending = true; return; }
        } else if (c == "RUN") { holdUntil = -1; held = false; r = "OK running"; }
        else r = "ERR unknown command";
        EnterCriticalSection(&lock); rep = r; LeaveCriticalSection(&lock);
        SetEvent(repEvent);
    }

    void shutdown() {
        if (running) { running = false; held = false; Server::get().remove(this); }
        for (int i = 0; i < 100 && inUse; i++) Sleep(20);   // let a waiting submit() leave
    }
};

// ---------------------------------------------------------------- server
std::string Server::handle(const std::string &line) {
    char cmd[16] = {0};
    sscanf_s(line.c_str(), "%15s", cmd, (unsigned)sizeof(cmd));
    std::string c = cmd;
    for (auto &ch : c) ch = (char)toupper((unsigned char)ch);
    Bridge *d = 0; std::vector<AnalogProbe *> a;
    EnterCriticalSection(&lk);
    if (!digs.empty()) d = digs[0];
    a = ans;
    LeaveCriticalSection(&lk);

    if (c == "PING") return "OK pong";
    if (c == "APINS" || c == "AREAD") {
        if (a.empty()) return "ERR no analog probe in this circuit";
        bool prefix = a.size() > 1;
        if (c == "APINS") {
            std::string r = "OK ";
            for (size_t i = 0; i < a.size(); i++) { std::string p = a[i]->pinList(prefix); r += (i && !p.empty() ? "," : "") + p; }
            return r;
        }
        for (auto *p : a) if (!p->hasData()) return "ERR no analog data yet";
        std::string r = "OK t=" + fmtSeconds(a[0]->time());
        for (auto *p : a) r += p->values(prefix);
        return r;
    }
    if (!d) {
        if (c == "TIME" && !a.empty()) return "OK " + fmtSeconds(a[0]->time());
        return "ERR no digital probe in this circuit";
    }
    return d->submit(line);
}

void Server::loop() {
    while (running) {
        HANDLE h = CreateNamedPipeA(name.c_str(), PIPE_ACCESS_DUPLEX,
                                    PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT, 1, 65536, 65536, 0, 0);
        if (h == INVALID_HANDLE_VALUE) { trace("CreateNamedPipe failed %lu", GetLastError()); return; }
        BOOL ok = ConnectNamedPipe(h, 0) ? TRUE : (GetLastError() == ERROR_PIPE_CONNECTED);
        std::string buf;
        while (ok && running) {
            char chunk[512]; DWORD n = 0;
            if (!ReadFile(h, chunk, sizeof(chunk), &n, 0) || n == 0) break;
            buf.append(chunk, n);
            size_t nl;
            while ((nl = buf.find('\n')) != std::string::npos) {
                std::string line = buf.substr(0, nl); buf.erase(0, nl + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                std::string answer = handle(line) + "\n";
                DWORD w = 0;
                WriteFile(h, answer.c_str(), (DWORD)answer.size(), &w, 0);
            }
        }
        DisconnectNamedPipe(h); CloseHandle(h);
    }
}

// Proteus builds BOTH a SPICE and a DSIM model for every part that uses this DLL, and it asks
// the SPICE side isanalog(pin) before setup(). Only the model whose kind matches the part's
// PRIMITIVE may claim its pins, so the kind is encoded in the model name (the text after the
// comma in PRIMITIVE): ANALOG,MCPBRIDGE_A is an analog probe, anything else (e.g.
// DIGITAL,MCPBRIDGE_D) is a digital probe.
static bool isAnalogDevice(const char *device) { return device && _stricmp(device, "MCPBRIDGE_A") == 0; }

extern "C" __declspec(dllexport) IDSIMMODEL *createdsimmodel(CHAR *device, ILICENCESERVER *ils) {
    BOOL ok = ils->authorize(0x00000000);
    trace("createdsimmodel device=%s authorize=%d", device ? device : "?", ok);
    if (ok) return new Bridge(!isAnalogDevice(device));
    return NULL;
}
extern "C" __declspec(dllexport) VOID deletedsimmodel(IDSIMMODEL *model) { delete (Bridge *)model; }

extern "C" __declspec(dllexport) ISPICEMODEL *createspicemodel(CHAR *device, ILICENCESERVER *ils) {
    BOOL ok = ils->authorize(0x00000000);
    trace("createspicemodel device=%s authorize=%d", device ? device : "?", ok);
    if (ok) return new AnalogProbe(isAnalogDevice(device));
    return NULL;
}
extern "C" __declspec(dllexport) VOID deletespicemodel(ISPICEMODEL *model) { delete (AnalogProbe *)model; }

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) trace("DLL loaded pid=%lu", GetCurrentProcessId());
    return TRUE;
}
