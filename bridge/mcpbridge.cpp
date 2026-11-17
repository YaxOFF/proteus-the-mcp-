// MCPBRIDGE.DLL - Proteus VSM digital model that exposes a live simulation to an
// external controller (our MCP server) over a Windows named pipe.
//
// Attach to any part:  PRIMITIVE=DIGITAL  MODDLL=MCPBRIDGE.DLL
// Optional part properties:
//   BRIDGEPINS = comma list of pin names to expose (default: try common names)
//   PIPE       = pipe name (default proteus_mcp)  -> \\.\pipe\<PIPE>
//   TICK       = service period in seconds of simulation time (default 0.001)
//   START      = RUN or HOLD (HOLD pauses at t=0 until a client sends RUN/STEP)
//
// Protocol: one ASCII line per request, one line per reply.
//   PING                     -> OK pong
//   TIME                     -> OK <seconds>
//   PINS                     -> OK A,B,Q
//   READ                     -> OK t=<s> A=1 B=0 Q=Z
//   SET <pin> <0|1|Z>        -> OK            (driven at the next service point)
//   EVENTS                   -> OK <t>:<pin>=<v>;...   (edges since last call)
//   HOLD                     -> OK held t=<s>
//   STEP <seconds>           -> OK held t=<s> (runs then holds again)
//   RUN                      -> OK running
#include <windows.h>
#include <stdio.h>
#include <string>
#include <vector>
#include <deque>
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

class Bridge : public IDSIMMODEL {
public:
    Bridge() : inst(0), ckt(0), now(0), holdUntil(-1), held(false), running(true),
               pipeThread(0), reqEvent(CreateEvent(0, FALSE, FALSE, 0)),
               repEvent(CreateEvent(0, FALSE, FALSE, 0)), hasReq(false) {
        InitializeCriticalSection(&lock);
    }
    ~Bridge() {
        shutdown();
        CloseHandle(reqEvent); CloseHandle(repEvent);
        DeleteCriticalSection(&lock);
    }

    INT isdigital(CHAR *) { return TRUE; }

    VOID setup(IINSTANCE *instance, IDSIMCKT *dsim) {
        inst = instance; ckt = dsim;
        CHAR *list = inst->getstrval((CHAR *)"BRIDGEPINS", (CHAR *)"");
        std::vector<std::string> candidates;
        std::string s = list ? list : "";
        if (s.empty()) {
            const char *defs[] = {"A","B","C","D","Q","IN","OUT","CLK","EN","RST","TX","RX","SDA","SCL",
                                  "D0","D1","D2","D3","D4","D5","D6","D7",
                                  "D8","D9","D10","D11","D12","D13","D14","D15",
                                  "1","2","3","4","5","6","7","8"};
            for (auto d : defs) candidates.push_back(d);
        } else {
            size_t p = 0;
            while (p <= s.size()) {
                size_t e = s.find(',', p); if (e == std::string::npos) e = s.size();
                std::string n = s.substr(p, e - p);
                if (!n.empty()) candidates.push_back(n);
                p = e + 1;
            }
        }
        for (auto &n : candidates) {
            IDSIMPIN *pin = inst->getdsimpin((CHAR *)n.c_str(), FALSE);
            if (pin) { names.push_back(n); pins.push_back(pin); last.push_back(pin->istate()); }
        }
        double tick = 0.001;
        inst->getnumval(&tick, (CHAR *)"TICK", 0.001);
        if (tick < 1e-9) tick = 1e-9;
        tickTicks = (ABSTIME)(tick * (double)TICKS_PER_SEC);
        CHAR *start = inst->getstrval((CHAR *)"START", (CHAR *)"RUN");
        if (start && _stricmp(start, "HOLD") == 0) holdUntil = 0;
        CHAR *pn = inst->getstrval((CHAR *)"PIPE", (CHAR *)"proteus_mcp");
        pipeName = std::string("\\\\.\\pipe\\") + (pn && *pn ? pn : "proteus_mcp");
        inst->log((CHAR *)"MCP bridge: %d pins on %s", (int)pins.size(), pipeName.c_str());
        trace("setup: %d pins on %s", (int)pins.size(), pipeName.c_str());
        running = true;
        pipeThread = CreateThread(0, 0, pipeMain, this, 0, 0);
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

private:
    IINSTANCE *inst; IDSIMCKT *ckt;
    std::vector<std::string> names; std::vector<IDSIMPIN *> pins; std::vector<STATE> last;
    std::vector<std::pair<size_t, STATE>> pendingDrives;
    std::deque<std::string> events;
    ABSTIME tickTicks, now, holdUntil; volatile bool held, running;
    std::string pipeName; HANDLE pipeThread, reqEvent, repEvent;
    CRITICAL_SECTION lock; std::string req, rep; volatile bool hasReq; volatile bool stepPending = false;

    void applyDrives(ABSTIME time) {
        EnterCriticalSection(&lock);
        auto todo = pendingDrives; pendingDrives.clear();
        LeaveCriticalSection(&lock);
        for (auto &d : todo) pins[d.first]->setstate(time, 1, d.second);
    }

    void serveOnce(ABSTIME time) {
        if (WaitForSingleObject(reqEvent, 0) == WAIT_OBJECT_0) process(time);
    }

    std::string fmtTime(ABSTIME t) { char b[48]; sprintf_s(b, "%.12g", (double)t / TICKS_PER_SEC); return b; }

    // Runs on the simulator thread: safe to touch pins and time.
    void process(ABSTIME time) {
        std::string r, q;
        EnterCriticalSection(&lock); q = req; LeaveCriticalSection(&lock);
        char cmd[16] = {0}, a1[64] = {0}, a2[16] = {0};
        sscanf_s(q.c_str(), "%15s %63s %15s", cmd, (unsigned)sizeof(cmd), a1, (unsigned)sizeof(a1), a2, (unsigned)sizeof(a2));
        std::string c = cmd;
        if (c == "PING") r = "OK pong";
        else if (c == "TIME") r = "OK " + fmtTime(time);
        else if (c == "PINS") { r = "OK "; for (size_t i = 0; i < names.size(); i++) r += (i ? "," : "") + names[i]; }
        else if (c == "READ") {
            r = "OK t=" + fmtTime(time);
            for (size_t i = 0; i < pins.size(); i++) { r += " " + names[i] + "="; r += stateChar(pins[i]->istate()); }
        } else if (c == "SET") {
            size_t i = 0; while (i < names.size() && _stricmp(names[i].c_str(), a1) != 0) i++;
            STATE st = a2[0] == '1' ? SHI : a2[0] == '0' ? SLO : (a2[0] == 'Z' || a2[0] == 'z') ? FLT : (STATE)0;
            if (i == names.size()) r = "ERR unknown pin";
            else if (!st && a2[0] != 'Z') r = "ERR value must be 0, 1 or Z";
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
        if (!running) return;
        running = false; held = false;
        if (pipeThread) {
            HANDLE h = CreateFileA(pipeName.c_str(), GENERIC_READ | GENERIC_WRITE, 0, 0, OPEN_EXISTING, 0, 0);
            if (h != INVALID_HANDLE_VALUE) CloseHandle(h);   // unblock ConnectNamedPipe
            WaitForSingleObject(pipeThread, 2000);
            CloseHandle(pipeThread); pipeThread = 0;
        }
    }

    static DWORD WINAPI pipeMain(LPVOID p) { ((Bridge *)p)->pipeLoop(); return 0; }

    void pipeLoop() {
        while (running) {
            HANDLE h = CreateNamedPipeA(pipeName.c_str(), PIPE_ACCESS_DUPLEX,
                                        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT, 1, 65536, 65536, 0, 0);
            if (h == INVALID_HANDLE_VALUE) return;
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
                    std::string answer;
                    EnterCriticalSection(&lock); req = line; LeaveCriticalSection(&lock);
                    ResetEvent(repEvent);
                    SetEvent(reqEvent);
                    // Answered by the simulator thread at its next service point.
                    // STEP may need long wall time to reach its target: allow 5 minutes.
                    DWORD limit = _strnicmp(line.c_str(), "STEP", 4) == 0 ? 300000 : 3000;
                    if (WaitForSingleObject(repEvent, limit) == WAIT_OBJECT_0) {
                        EnterCriticalSection(&lock); answer = rep; LeaveCriticalSection(&lock);
                    } else {
                        ResetEvent(reqEvent);
                        answer = "ERR simulation not running";
                    }
                    answer += "\n"; DWORD w = 0;
                    WriteFile(h, answer.c_str(), (DWORD)answer.size(), &w, 0);
                }
            }
            DisconnectNamedPipe(h); CloseHandle(h);
        }
    }
};

extern "C" __declspec(dllexport) IDSIMMODEL *createdsimmodel(CHAR *device, ILICENCESERVER *ils) {
    BOOL ok = ils->authorize(0x00000000);
    trace("createdsimmodel device=%s authorize=%d", device ? device : "?", ok);
    if (ok) return new Bridge;
    return NULL;
}
extern "C" __declspec(dllexport) VOID deletedsimmodel(IDSIMMODEL *model) { delete (Bridge *)model; }

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) trace("DLL loaded pid=%lu", GetCurrentProcessId());
    return TRUE;
}
