// TodoList.cpp — native C++/Win32 port of TodoList.py (functionality-identical).
// Windows twin of TodoList.mm (the macOS C++/Cocoa port).
//
// Build:  .\build_todolist_native.ps1   (produces .\TodoList.exe, an x64 PE)
//
// The data layer is pure C++ (testable headless — see
// tests/test_todolist_native.cpp, which #includes this file with
// TODOLIST_TESTING defined); the UI layer is Win32. JSON goes through a
// hand-rolled reader/writer that reproduces Python's json.dump(indent=2)
// output byte-for-byte (ensure_ascii \uXXXX escapes, CRLF line ends as
// Python's text mode writes on Windows, raw number tokens passed through
// verbatim) — other machines still run TodoList.py or the macOS TodoList.exe
// against the same OneDrive-synced todos.json, so every implementation must
// round-trip the files the others write.
//
// todos.json lives in <OneDrive>/MyAppShare (TODOLIST_DATA_DIR override,
// exe-dir fallback), same resolution rules as TodoList.py including the
// one-way fold of the legacy <OneDrive>/TodoList dir. todo_state.json
// (geometry/filters/sort) is per-machine, shared with the Python version —
// same schema, same location (the executable's directory).

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define _CRT_SECURE_NO_WARNINGS  // _wfopen is fine — error handling is explicit
#include <windows.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <functional>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "comctl32.lib")

// ── UTF-8 <-> UTF-16 (std::string is UTF-8 everywhere; wide only at APIs) ──

static std::wstring widen(const std::string &s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

static std::string narrow(const std::wstring &w) {
    if (w.empty()) return "";
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), nullptr, 0, nullptr, nullptr);
    std::string s((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), &s[0], n, nullptr, nullptr);
    return s;
}

// ── Filesystem helpers ────────────────────────────────────────────────

static std::string pathJoin(const std::string &a, const std::string &b) {
    if (a.empty()) return b;
    char last = a.back();
    if (last == '\\' || last == '/') return a + b;
    return a + "\\" + b;
}

static std::string dirName(const std::string &p) {
    auto pos = p.find_last_of("\\/");
    if (pos == std::string::npos) return "";
    if (pos == 0) return p.substr(0, 1);
    if (pos == 2 && p[1] == ':') return p.substr(0, 3);  // "C:\x" -> "C:\"
    return p.substr(0, pos);
}

static std::string baseName(const std::string &p) {
    auto pos = p.find_last_of("\\/");
    return pos == std::string::npos ? p : p.substr(pos + 1);
}

static bool fileExists(const std::string &p) {
    return GetFileAttributesW(widen(p).c_str()) != INVALID_FILE_ATTRIBUTES;
}

static bool isDir(const std::string &p) {
    DWORD a = GetFileAttributesW(widen(p).c_str());
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

static bool makeDirs(const std::string &p) {  // mkdir -p semantics
    if (p.empty()) return false;
    std::wstring w = widen(p), cur;
    for (size_t i = 0; i < w.size(); ++i) {
        wchar_t ch = (w[i] == L'/') ? L'\\' : w[i];
        cur += ch;
        bool atComponentEnd = (i + 1 == w.size()) || w[i + 1] == L'\\' || w[i + 1] == L'/';
        if (atComponentEnd && ch != L'\\' && !(cur.size() == 2 && cur[1] == L':'))
            CreateDirectoryW(cur.c_str(), nullptr);  // exists already -> harmless
    }
    return isDir(p);
}

// mtime as FILETIME 100ns ticks — full stat precision, the analog of
// os.path.getmtime. nullopt = file absent / unstattable, matching Python's
// None sentinel.
typedef std::optional<unsigned long long> Mtime;

static Mtime statMtime(const std::string &p) {
    WIN32_FILE_ATTRIBUTE_DATA fad;
    if (!GetFileAttributesExW(widen(p).c_str(), GetFileExInfoStandard, &fad)) return std::nullopt;
    ULARGE_INTEGER u;
    u.LowPart = fad.ftLastWriteTime.dwLowDateTime;
    u.HighPart = fad.ftLastWriteTime.dwHighDateTime;
    return u.QuadPart;
}

static std::vector<std::string> listDir(const std::string &dir) {
    std::vector<std::string> names;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(widen(pathJoin(dir, "*")).c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return names;
    do {
        std::string n = narrow(fd.cFileName);
        if (n != "." && n != "..") names.push_back(n);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return names;
}

// fnmatch analog for the fork pattern ("todos-*.json"): * and ? only,
// case-insensitive like Windows filename globbing.
static bool wildcardMatch(const std::string &pat, const std::string &name) {
    auto low = [](char c) { return (char)tolower((unsigned char)c); };
    size_t p = 0, n = 0, star = std::string::npos, mark = 0;
    while (n < name.size()) {
        if (p < pat.size() && (pat[p] == '?' || low(pat[p]) == low(name[n]))) {
            p++; n++;
        } else if (p < pat.size() && pat[p] == '*') {
            star = p++; mark = n;
        } else if (star != std::string::npos) {
            p = star + 1; n = ++mark;
        } else {
            return false;
        }
    }
    while (p < pat.size() && pat[p] == '*') p++;
    return p == pat.size();
}

static std::optional<std::string> readFileBytes(const std::string &p) {
    FILE *f = _wfopen(widen(p).c_str(), L"rb");
    if (!f) return std::nullopt;
    std::string out;
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) out.append(buf, n);
    bool ok = !ferror(f);
    fclose(f);
    if (!ok) return std::nullopt;
    return out;
}

static bool writeFileBytes(const std::string &p, const std::string &data) {
    FILE *f = _wfopen(widen(p).c_str(), L"wb");
    if (!f) return false;
    size_t n = fwrite(data.data(), 1, data.size(), f);
    bool ok = (n == data.size()) && fclose(f) == 0;
    if (n != data.size()) fclose(f);
    return ok;
}

// os.rename parity: Windows os.rename refuses an existing target
static bool renameNoReplace(const std::string &from, const std::string &to) {
    return MoveFileExW(widen(from).c_str(), widen(to).c_str(), 0) != 0;
}

// os.replace parity: atomic on the same volume, target overwritten
static bool replaceFile(const std::string &from, const std::string &to) {
    return MoveFileExW(widen(from).c_str(), widen(to).c_str(), MOVEFILE_REPLACE_EXISTING) != 0;
}

static bool copyFileBytes(const std::string &src, const std::string &dst) {
    return CopyFileW(widen(src).c_str(), widen(dst).c_str(), FALSE) != 0;
}

static void removeFile(const std::string &p) { DeleteFileW(widen(p).c_str()); }

static void removeDirIfEmpty(const std::string &p) {  // os.rmdir best-effort
    RemoveDirectoryW(widen(p).c_str());
}

static std::string getEnvVar(const char *name) {  // "" = unset (or empty — both falsy in the .py)
    wchar_t buf[4096];
    DWORD n = GetEnvironmentVariableW(widen(name).c_str(), buf, 4096);
    if (n == 0 || n >= 4096) return "";
    return narrow(std::wstring(buf, n));
}

// ── Date handling ─────────────────────────────────────────────────────

struct Ymd {
    int y, m, d;
    bool operator<(const Ymd &o) const {
        if (y != o.y) return y < o.y;
        if (m != o.m) return m < o.m;
        return d < o.d;
    }
    bool operator==(const Ymd &o) const { return y == o.y && m == o.m && d == o.d; }
};

static bool isLeap(int y) { return (y % 4 == 0 && y % 100 != 0) || y % 400 == 0; }

static int daysInMonth(int y, int m) {
    static const int base[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (m == 2 && isLeap(y)) return 29;
    return base[m - 1];
}

static bool validYmd(int y, int m, int d) {
    // Python datetime range: year 1..9999, strict calendar validation
    return y >= 1 && y <= 9999 && m >= 1 && m <= 12 && d >= 1 && d <= daysInMonth(y, m);
}

// Split "a<sep>b<sep>c" into three all-digit fields (1-2, 1-2, 1-4 digits in
// the order given). Mirrors what strptime's %d/%m/%Y actually accepts.
static bool splitThree(const std::string &s, char sep, size_t maxA, size_t maxB, size_t maxC,
                       long *a, long *b, long *c) {
    size_t p1 = s.find(sep);
    if (p1 == std::string::npos) return false;
    size_t p2 = s.find(sep, p1 + 1);
    if (p2 == std::string::npos || s.find(sep, p2 + 1) != std::string::npos) return false;
    std::string fa = s.substr(0, p1), fb = s.substr(p1 + 1, p2 - p1 - 1), fc = s.substr(p2 + 1);
    auto digits = [](const std::string &f, size_t maxLen) {
        if (f.empty() || f.size() > maxLen) return false;
        for (char ch : f)
            if (ch < '0' || ch > '9') return false;
        return true;
    };
    if (!digits(fa, maxA) || !digits(fb, maxB) || !digits(fc, maxC)) return false;
    *a = strtol(fa.c_str(), nullptr, 10);
    *b = strtol(fb.c_str(), nullptr, 10);
    *c = strtol(fc.c_str(), nullptr, 10);
    return true;
}

static std::string stripped(const std::string &s) {
    size_t b = s.find_first_not_of(" \t\r\n\f\v");
    if (b == std::string::npos) return "";
    size_t e = s.find_last_not_of(" \t\r\n\f\v");
    return s.substr(b, e - b + 1);
}

// The Python _parse_date: try %d/%m/%Y, %Y-%m-%d, %m/%d/%Y in order on the
// stripped string; strict calendar validation (12/25/2026 fails the first
// format on month=25 and falls through to the US format, exactly as strptime
// cascades).
static std::optional<Ymd> parseDate(const std::string &raw) {
    std::string s = stripped(raw);
    if (s.empty()) return std::nullopt;
    long a, b, c;
    if (splitThree(s, '/', 2, 2, 4, &a, &b, &c) && validYmd((int)c, (int)b, (int)a))
        return Ymd{(int)c, (int)b, (int)a};  // %d/%m/%Y
    if (splitThree(s, '-', 4, 2, 2, &a, &b, &c) && validYmd((int)a, (int)b, (int)c))
        return Ymd{(int)a, (int)b, (int)c};  // %Y-%m-%d
    if (splitThree(s, '/', 2, 2, 4, &a, &b, &c) && validYmd((int)c, (int)a, (int)b))
        return Ymd{(int)c, (int)a, (int)b};  // %m/%d/%Y
    return std::nullopt;
}

static Ymd todayYmd() {
    time_t t = time(nullptr);
    struct tm lt;
    localtime_s(&lt, &t);
    return Ymd{lt.tm_year + 1900, lt.tm_mon + 1, lt.tm_mday};
}

static std::string nowCreatedString() {  // strftime("%d/%m/%Y")
    time_t t = time(nullptr);
    struct tm lt;
    localtime_s(&lt, &t);
    char buf[16];
    snprintf(buf, sizeof(buf), "%02d/%02d/%04d", lt.tm_mday, lt.tm_mon + 1, lt.tm_year + 1900);
    return buf;
}

// ── JSON (Python-json parity) ─────────────────────────────────────────
// Insertion-ordered objects (Python dicts), number tokens kept verbatim (the
// app itself only ever creates strings and bools — any number in the file
// came from another writer, and passing the token through is the highest-
// fidelity round trip). Unknown keys written by a future TodoList.py survive
// a load->save on this machine, exactly as Python's dicts do.

struct JsonValue {
    enum Type { Null, Bool, Number, String, Array, Object };
    Type type = Null;
    bool boolean = false;
    std::string number;  // raw token, written back verbatim
    std::string str;     // UTF-8 (WTF-8 for lone surrogates, which re-escape on write)
    std::vector<JsonValue> array;
    std::vector<std::pair<std::string, JsonValue>> object;

    static JsonValue makeNull() { return JsonValue(); }
    static JsonValue makeBool(bool b) {
        JsonValue v; v.type = Bool; v.boolean = b; return v;
    }
    static JsonValue makeString(const std::string &s) {
        JsonValue v; v.type = String; v.str = s; return v;
    }
    static JsonValue makeArray() {
        JsonValue v; v.type = Array; return v;
    }
    static JsonValue makeObject() {
        JsonValue v; v.type = Object; return v;
    }

    const JsonValue *find(const char *key) const {
        if (type != Object) return nullptr;
        for (const auto &kv : object)
            if (kv.first == key) return &kv.second;
        return nullptr;
    }
    JsonValue *find(const char *key) {
        if (type != Object) return nullptr;
        for (auto &kv : object)
            if (kv.first == key) return &kv.second;
        return nullptr;
    }
    // dict semantics: an existing key keeps its position, value replaced
    void set(const std::string &key, JsonValue v) {
        for (auto &kv : object)
            if (kv.first == key) { kv.second = std::move(v); return; }
        object.emplace_back(key, std::move(v));
    }
};

// Python truthiness (the .py filters on `todo["done"]`, not `== True`)
static bool jsonTruthy(const JsonValue &v) {
    switch (v.type) {
        case JsonValue::Null: return false;
        case JsonValue::Bool: return v.boolean;
        case JsonValue::Number: return strtod(v.number.c_str(), nullptr) != 0.0;
        case JsonValue::String: return !v.str.empty();
        case JsonValue::Array: return !v.array.empty();
        case JsonValue::Object: return !v.object.empty();
    }
    return false;
}

// Append a Unicode code point as UTF-8 (surrogate values pass through as
// 3-byte WTF-8 so a Python-written lone-surrogate escape round-trips).
static void appendUtf8(std::string &out, unsigned cp) {
    if (cp < 0x80) {
        out += (char)cp;
    } else if (cp < 0x800) {
        out += (char)(0xC0 | (cp >> 6));
        out += (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        out += (char)(0xE0 | (cp >> 12));
        out += (char)(0x80 | ((cp >> 6) & 0x3F));
        out += (char)(0x80 | (cp & 0x3F));
    } else {
        out += (char)(0xF0 | (cp >> 18));
        out += (char)(0x80 | ((cp >> 12) & 0x3F));
        out += (char)(0x80 | ((cp >> 6) & 0x3F));
        out += (char)(0x80 | (cp & 0x3F));
    }
}

struct JsonParser {
    const char *p, *end;
    int depth = 0;

    explicit JsonParser(const std::string &text) : p(text.data()), end(text.data() + text.size()) {
        if (end - p >= 3 && (unsigned char)p[0] == 0xEF && (unsigned char)p[1] == 0xBB &&
            (unsigned char)p[2] == 0xBF)
            p += 3;  // tolerate an editor-added BOM (NSJSONSerialization does too)
    }

    void skipWs() {
        while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    }
    bool lit(const char *s) {
        size_t n = strlen(s);
        if ((size_t)(end - p) < n || memcmp(p, s, n) != 0) return false;
        p += n;
        return true;
    }

    bool parseString(std::string &out) {
        if (p >= end || *p != '"') return false;
        p++;
        while (p < end) {
            unsigned char c = (unsigned char)*p;
            if (c == '"') { p++; return true; }
            if (c == '\\') {
                p++;
                if (p >= end) return false;
                char e = *p++;
                switch (e) {
                    case '"': out += '"'; break;
                    case '\\': out += '\\'; break;
                    case '/': out += '/'; break;
                    case 'b': out += '\b'; break;
                    case 'f': out += '\f'; break;
                    case 'n': out += '\n'; break;
                    case 'r': out += '\r'; break;
                    case 't': out += '\t'; break;
                    case 'u': {
                        unsigned cp;
                        if (!hex4(&cp)) return false;
                        if (cp >= 0xD800 && cp <= 0xDBFF && end - p >= 6 && p[0] == '\\' &&
                            p[1] == 'u') {
                            const char *save = p;
                            p += 2;
                            unsigned lo;
                            if (hex4(&lo) && lo >= 0xDC00 && lo <= 0xDFFF)
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            else
                                p = save;  // not a pair — keep the lone surrogate (Python does)
                        }
                        appendUtf8(out, cp);
                        break;
                    }
                    default: return false;  // Python json: invalid \x escape
                }
            } else if (c < 0x20) {
                return false;  // Python json strict=True rejects raw control chars
            } else {
                out += (char)c;
                p++;
            }
        }
        return false;  // unterminated
    }

    bool hex4(unsigned *out) {
        if (end - p < 4) return false;
        unsigned v = 0;
        for (int i = 0; i < 4; i++) {
            char c = p[i];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= (unsigned)(c - '0');
            else if (c >= 'a' && c <= 'f') v |= (unsigned)(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= (unsigned)(c - 'A' + 10);
            else return false;
        }
        p += 4;
        *out = v;
        return true;
    }

    // Strict number grammar (json.loads rejects 01 / +1 / .5 / 5. / 1.e5)
    bool parseNumber(std::string &tok) {
        const char *start = p;
        if (p < end && *p == '-') p++;
        if (p >= end) return false;
        if (*p == '0') {
            p++;
        } else if (*p >= '1' && *p <= '9') {
            while (p < end && *p >= '0' && *p <= '9') p++;
        } else {
            return false;
        }
        if (p < end && *p == '.') {
            p++;
            if (p >= end || *p < '0' || *p > '9') return false;
            while (p < end && *p >= '0' && *p <= '9') p++;
        }
        if (p < end && (*p == 'e' || *p == 'E')) {
            p++;
            if (p < end && (*p == '+' || *p == '-')) p++;
            if (p >= end || *p < '0' || *p > '9') return false;
            while (p < end && *p >= '0' && *p <= '9') p++;
        }
        tok.assign(start, (size_t)(p - start));
        return true;
    }

    bool parseValue(JsonValue &out) {
        if (++depth > 128) return false;
        skipWs();
        if (p >= end) { depth--; return false; }
        bool ok = false;
        char c = *p;
        if (c == '{') {
            p++;
            out.type = JsonValue::Object;
            skipWs();
            if (p < end && *p == '}') { p++; ok = true; }
            else {
                while (true) {
                    skipWs();
                    std::string key;
                    if (!parseString(key)) break;
                    skipWs();
                    if (p >= end || *p != ':') break;
                    p++;
                    JsonValue v;
                    if (!parseValue(v)) break;
                    out.set(key, std::move(v));  // duplicate key: last value wins, like dict
                    skipWs();
                    if (p < end && *p == ',') { p++; continue; }
                    if (p < end && *p == '}') { p++; ok = true; }
                    break;
                }
            }
        } else if (c == '[') {
            p++;
            out.type = JsonValue::Array;
            skipWs();
            if (p < end && *p == ']') { p++; ok = true; }
            else {
                while (true) {
                    JsonValue v;
                    if (!parseValue(v)) break;
                    out.array.push_back(std::move(v));
                    skipWs();
                    if (p < end && *p == ',') { p++; continue; }
                    if (p < end && *p == ']') { p++; ok = true; }
                    break;
                }
            }
        } else if (c == '"') {
            out.type = JsonValue::String;
            ok = parseString(out.str);
        } else if (lit("null")) {
            out.type = JsonValue::Null;
            ok = true;
        } else if (lit("true")) {
            out = JsonValue::makeBool(true);
            ok = true;
        } else if (lit("false")) {
            out = JsonValue::makeBool(false);
            ok = true;
        } else if (lit("NaN")) {  // Python json accepts and emits these
            out.type = JsonValue::Number;
            out.number = "NaN";
            ok = true;
        } else if (lit("Infinity")) {
            out.type = JsonValue::Number;
            out.number = "Infinity";
            ok = true;
        } else if (c == '-' && lit("-Infinity")) {
            out.type = JsonValue::Number;
            out.number = "-Infinity";
            ok = true;
        } else if (c == '-' || (c >= '0' && c <= '9')) {
            out.type = JsonValue::Number;
            ok = parseNumber(out.number);
        }
        depth--;
        return ok;
    }
};

static bool jsonParse(const std::string &text, JsonValue &out) {
    out = JsonValue();  // a failed parse must leave no residue in a reused value
    JsonParser jp(text);
    if (!jp.parseValue(out)) return false;
    jp.skipWs();
    return jp.p == jp.end;  // trailing garbage -> JSONDecodeError parity
}

// json.dump's default ensure_ascii escaping: everything outside 0x20..0x7E
// becomes \uxxxx (lowercase hex, surrogate pairs for astral planes), plus
// the short escapes for the usual suspects.
static void appendPyEscaped(std::string &out, const std::string &utf8) {
    out += '"';
    size_t i = 0, n = utf8.size();
    while (i < n) {
        unsigned char c = (unsigned char)utf8[i];
        unsigned cp;
        if (c < 0x80) {
            cp = c;
            i += 1;
        } else if ((c & 0xE0) == 0xC0 && i + 1 < n) {
            cp = ((unsigned)(c & 0x1F) << 6) | (unsigned)(utf8[i + 1] & 0x3F);
            i += 2;
        } else if ((c & 0xF0) == 0xE0 && i + 2 < n) {
            cp = ((unsigned)(c & 0x0F) << 12) | ((unsigned)(utf8[i + 1] & 0x3F) << 6) |
                 (unsigned)(utf8[i + 2] & 0x3F);
            i += 3;
        } else if ((c & 0xF8) == 0xF0 && i + 3 < n) {
            cp = ((unsigned)(c & 0x07) << 18) | ((unsigned)(utf8[i + 1] & 0x3F) << 12) |
                 ((unsigned)(utf8[i + 2] & 0x3F) << 6) | (unsigned)(utf8[i + 3] & 0x3F);
            i += 4;
        } else {
            cp = c;  // invalid byte — pass as U+00xx rather than crash
            i += 1;
        }
        char buf[16];
        switch (cp) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (cp >= 0x20 && cp <= 0x7E) {
                    out += (char)cp;
                } else if (cp <= 0xFFFF) {
                    snprintf(buf, sizeof(buf), "\\u%04x", cp);
                    out += buf;
                } else {
                    unsigned v = cp - 0x10000;
                    snprintf(buf, sizeof(buf), "\\u%04x\\u%04x", 0xD800 + (v >> 10),
                             0xDC00 + (v & 0x3FF));
                    out += buf;
                }
        }
    }
    out += '"';
}

// json.dump(data, f, indent=2) parity, with CRLF structural newlines —
// Python's text-mode file on Windows translates every '\n' to "\r\n", so
// this is byte-for-byte what TodoList.py writes on this platform.
static const char *JSON_NL = "\r\n";

static void dumpJsonValue(const JsonValue &v, std::string &out, int level) {
    auto indent = [&](int lv) {
        out += JSON_NL;
        out.append((size_t)lv * 2, ' ');
    };
    switch (v.type) {
        case JsonValue::Null: out += "null"; break;
        case JsonValue::Bool: out += v.boolean ? "true" : "false"; break;
        case JsonValue::Number: out += v.number; break;
        case JsonValue::String: appendPyEscaped(out, v.str); break;
        case JsonValue::Array:
            if (v.array.empty()) { out += "[]"; break; }
            out += '[';
            for (size_t i = 0; i < v.array.size(); i++) {
                if (i) out += ',';
                indent(level + 1);
                dumpJsonValue(v.array[i], out, level + 1);
            }
            indent(level);
            out += ']';
            break;
        case JsonValue::Object:
            if (v.object.empty()) { out += "{}"; break; }
            out += '{';
            for (size_t i = 0; i < v.object.size(); i++) {
                if (i) out += ',';
                indent(level + 1);
                appendPyEscaped(out, v.object[i].first);
                out += ": ";
                dumpJsonValue(v.object[i].second, out, level + 1);
            }
            indent(level);
            out += '}';
            break;
    }
}

static std::string dumpsPython(const JsonValue &v) {
    std::string out;
    dumpJsonValue(v, out, 0);
    return out;
}

static std::optional<JsonValue> jsonReadFile(const std::string &path) {  // nullopt on any error
    auto bytes = readFileBytes(path);
    if (!bytes) return std::nullopt;
    JsonValue v;
    if (!jsonParse(*bytes, v)) return std::nullopt;
    return v;
}

// ── Shared-data path resolution (mirrors TodoList.py exactly) ─────────

static const char *SHARED_SUBDIR = "MyAppShare";
static const char *LEGACY_SHARED_SUBDIR = "TodoList";  // pre-2026-07-19 home
static const int DATA_POLL_MS = 5000;

typedef std::function<std::string(const char *)> EnvLookup;

// Windows branch of _find_onedrive_root: the sync client publishes its root
// in the OneDrive / OneDriveConsumer / OneDriveCommercial env vars.
static std::string findOneDriveRoot(const EnvLookup &env) {
    for (const char *var : {"OneDrive", "OneDriveConsumer", "OneDriveCommercial"}) {
        std::string root = env(var);
        if (!root.empty() && isDir(root)) return root;
    }
    return "";
}

// One-way fold of <OneDrive>/TodoList/todos.json into the shared dir. If the
// target slot is taken, the old file moves in under a conflict-fork name
// (todos-TodoListLegacy.json, then ...Legacy2, 3, ...) and fork absorption
// merges it. Best-effort; the rmdir only succeeds once the old dir is empty.
static void migrateLegacyDir(const std::string &onedrive, const std::string &sharedDir) {
    std::string legacy = pathJoin(pathJoin(onedrive, LEGACY_SHARED_SUBDIR), "todos.json");
    if (!fileExists(legacy)) return;
    std::string target = pathJoin(sharedDir, "todos.json");
    if (fileExists(target)) {
        int n = 0;  // 0 = bare "TodoListLegacy", then 2, 3, ... (Python's "" / 2 / +1)
        auto candidate = [&](int i) {
            char suffix[32] = "";
            if (i) snprintf(suffix, sizeof(suffix), "%d", i);
            return pathJoin(sharedDir, std::string("todos-TodoListLegacy") + suffix + ".json");
        };
        while (fileExists(candidate(n))) n = (n == 0) ? 2 : n + 1;
        target = candidate(n);
    }
    if (!renameNoReplace(legacy, target)) return;
    removeDirIfEmpty(dirName(legacy));  // best-effort, like the Python except-pass
}

// _resolve_data_file: TODOLIST_DATA_DIR override, else <OneDrive>/MyAppShare
// (folding in the legacy dir), else the executable's directory. First run
// with an empty shared slot seeds it from the local file.
static std::string resolveDataFile(const std::string &baseDir, const EnvLookup &env,
                                   const std::string &overrideDir) {
    std::string local = pathJoin(baseDir, "todos.json");
    std::string sharedDir = overrideDir;
    std::string onedrive;
    if (sharedDir.empty()) {
        onedrive = findOneDriveRoot(env);
        if (onedrive.empty()) return local;
        sharedDir = pathJoin(onedrive, SHARED_SUBDIR);
    }
    if (!makeDirs(sharedDir)) return local;
    if (!onedrive.empty())  // not under an explicit override — fold in the old home
        migrateLegacyDir(onedrive, sharedDir);
    std::string shared = pathJoin(sharedDir, "todos.json");
    if (!fileExists(shared) && fileExists(local)) copyFileBytes(local, shared);
    return shared;
}

// ── JSON model I/O ────────────────────────────────────────────────────
// todos: vector of Object JsonValues — kept as JSON objects (not a C++
// struct) so unknown keys written by a future TodoList.py survive a
// load→save round trip on this machine, exactly as Python's dicts do.

static std::string todoStr(const JsonValue &todo, const char *key) {
    const JsonValue *v = todo.find(key);
    return (v && v->type == JsonValue::String) ? v->str : "";
}

static bool todoDone(const JsonValue &todo) {
    const JsonValue *v = todo.find("done");
    return v && jsonTruthy(*v);
}

enum class LoadResult { Loaded, Missing, Corrupt };

// _load_data contract: missing file → mtime None, keep in-memory todos;
// unreadable/corrupt (possibly a half-synced cloud write) → remember the
// mtime so the poll stops re-reading it, keep in-memory todos; success →
// adopt todos, adopt categories only when the saved list is non-empty.
static LoadResult loadData(const std::string &path, std::vector<JsonValue> &todos,
                           std::vector<std::string> &categories, Mtime *mtime) {
    if (!fileExists(path)) {
        *mtime = std::nullopt;
        return LoadResult::Missing;
    }
    auto doc = jsonReadFile(path);
    *mtime = statMtime(path);
    if (!doc || doc->type != JsonValue::Object) return LoadResult::Corrupt;
    todos.clear();
    if (const JsonValue *t = doc->find("todos"); t && t->type == JsonValue::Array)
        for (const auto &item : t->array)
            if (item.type == JsonValue::Object) todos.push_back(item);
    if (const JsonValue *cats = doc->find("categories");
        cats && cats->type == JsonValue::Array && !cats->array.empty()) {
        categories.clear();
        for (const auto &c : cats->array)
            if (c.type == JsonValue::String) categories.push_back(c.str);
    }
    return LoadResult::Loaded;
}

// Atomic write via .tmp + replace, recreating the parent first — OneDrive
// garbage-collects a still-empty shared dir within seconds of creation
// (observed 2026-07-18), so the first save on a fresh machine may find its
// parent gone. Returns false best-effort, never throws.
static bool writeDataFile(const std::string &path, const std::vector<JsonValue> &todos,
                          const std::vector<std::string> &categories, Mtime *mtimeOut) {
    JsonValue doc = JsonValue::makeObject();
    JsonValue tarr = JsonValue::makeArray();
    tarr.array = todos;
    JsonValue carr = JsonValue::makeArray();
    for (const auto &c : categories) carr.array.push_back(JsonValue::makeString(c));
    doc.set("todos", std::move(tarr));
    doc.set("categories", std::move(carr));
    std::string text = dumpsPython(doc);
    makeDirs(dirName(path));
    std::string tmp = path + ".tmp";
    if (!writeFileBytes(tmp, text)) return false;
    if (!replaceFile(tmp, path)) {  // atomic — OneDrive never sees a half-write
        removeFile(tmp);
        return false;
    }
    if (mtimeOut) *mtimeOut = statMtime(path);
    return true;
}

// Identity key for fork merging: (text, created), with "key absent" distinct
// from "empty string" exactly like Python's (None, "") tuples.
static std::string todoIdentity(const JsonValue &todo) {
    const JsonValue *t = todo.find("text"), *c = todo.find("created");
    std::string key = (t && t->type == JsonValue::String) ? "P" + t->str : "M";
    key += '\x1f';
    key += (c && c->type == JsonValue::String) ? "P" + c->str : "M";
    return key;
}

// _absorb_conflict_forks: OneDrive resolves a concurrent write by renaming
// the losing machine's copy to todos-<ComputerName>.json beside the main
// file. Fold every fork's unique items back in (winner's version kept for
// items in both), union categories, delete the fork. Idempotent, so
// concurrent absorbers on different machines converge.
static bool absorbForks(const std::string &dataFile, std::vector<JsonValue> &todos,
                        std::vector<std::string> &categories) {
    std::string dir = dirName(dataFile);
    std::string base = baseName(dataFile);
    auto dot = base.rfind('.');
    if (dot != std::string::npos) base = base.substr(0, dot);
    std::string pattern = base + "-*.json";

    std::vector<std::string> forks;
    for (const auto &n : listDir(dir))
        if (wildcardMatch(pattern, n)) forks.push_back(pathJoin(dir, n));
    if (forks.empty()) return false;

    bool changed = false;
    std::set<std::string> seen;
    for (const auto &t : todos) seen.insert(todoIdentity(t));

    for (const auto &fork : forks) {
        auto doc = jsonReadFile(fork);
        if (!doc || doc->type != JsonValue::Object)
            continue;  // unreadable / half-synced — retry next poll
        if (const JsonValue *ft = doc->find("todos"); ft && ft->type == JsonValue::Array) {
            for (const auto &item : ft->array) {
                if (item.type != JsonValue::Object) continue;
                std::string key = todoIdentity(item);
                if (seen.find(key) == seen.end()) {
                    todos.push_back(item);
                    seen.insert(key);
                    changed = true;
                }
            }
        }
        if (const JsonValue *fc = doc->find("categories"); fc && fc->type == JsonValue::Array) {
            for (const auto &c : fc->array) {
                if (c.type == JsonValue::String &&
                    std::find(categories.begin(), categories.end(), c.str) == categories.end()) {
                    categories.push_back(c.str);
                    changed = true;
                }
            }
        }
        removeFile(fork);  // the delete syncs, clearing the fork everywhere
    }
    return changed;
}

// ── Filtering & sorting (mirrors _refresh_tree / _sort_key) ───────────

static bool passesFilter(const JsonValue &todo, const std::string &filterDone,
                         const std::string &filterPriority, const std::string &filterCategory) {
    bool done = todoDone(todo);
    if (filterDone == "active" && done) return false;
    if (filterDone == "completed" && !done) return false;
    if (filterPriority != "All" && todoStr(todo, "priority") != filterPriority) return false;
    if (filterCategory != "All" && todoStr(todo, "category") != filterCategory) return false;
    return true;
}

static int priorityRank(const JsonValue &todo) {
    std::string p = todoStr(todo, "priority");
    if (p == "High") return 0;
    if (p == "Medium") return 1;
    if (p == "Low") return 2;
    return 9;
}

static Ymd dateRank(const JsonValue &todo, const char *key) {
    auto d = parseDate(todoStr(todo, key));
    return d ? *d : Ymd{10000, 0, 0};  // sorts after every valid date, like datetime.max
}

// str.lower() analog: Unicode default lowercasing via LCMapStringEx, then a
// plain code-unit compare (the same literal ordering Python's str < uses).
static std::wstring lowerW(const std::wstring &w) {
    if (w.empty()) return w;
    int n = LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, w.data(), (int)w.size(),
                          nullptr, 0, nullptr, nullptr, 0);
    if (n <= 0) return w;
    std::wstring out((size_t)n, L'\0');
    LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, w.data(), (int)w.size(), &out[0], n,
                  nullptr, nullptr, 0);
    return out;
}

// strict-weak "a sorts before b" for one column, ascending
static bool todoLess(const std::string &col, const JsonValue &a, const JsonValue &b) {
    if (col == "done") return (todoDone(a) ? 0 : 1) < (todoDone(b) ? 0 : 1);
    if (col == "priority") return priorityRank(a) < priorityRank(b);
    if (col == "due" || col == "created") {
        const char *key = col == "due" ? "due" : "created";
        return dateRank(a, key) < dateRank(b, key);
    }
    if (col == "text" || col == "category") {
        const char *key = col == "text" ? "text" : "category";
        return lowerW(widen(todoStr(a, key))) < lowerW(widen(todoStr(b, key)));
    }
    return false;  // unknown column: todo.get(col, "") made every key equal in Python
}

struct VisibleRow {
    size_t realIndex;
    const JsonValue *todo;
};

// Filter + optional sort. std::stable_sort with the reversed comparator
// reproduces Python's list.sort(key=..., reverse=...) exactly, including
// original-order preservation among equal keys in both directions.
static std::vector<VisibleRow> visibleRows(const std::vector<JsonValue> &todos,
                                           const std::string &filterDone,
                                           const std::string &filterPriority,
                                           const std::string &filterCategory,
                                           const std::string &sortCol, bool sortReverse) {
    std::vector<VisibleRow> rows;
    for (size_t i = 0; i < todos.size(); i++)
        if (passesFilter(todos[i], filterDone, filterPriority, filterCategory))
            rows.push_back({i, &todos[i]});
    if (!sortCol.empty()) {
        std::stable_sort(rows.begin(), rows.end(), [&](const VisibleRow &x, const VisibleRow &y) {
            return sortReverse ? todoLess(sortCol, *y.todo, *x.todo)
                               : todoLess(sortCol, *x.todo, *y.todo);
        });
    }
    return rows;
}

// ── Per-machine UI state (todo_state.json — same schema as the .py) ───

struct UiState {
    std::string geometry;  // Tk-style "WxH+X+Y" ("" = none saved)
    std::string filterDone = "all";
    std::string filterPriority = "All";
    std::string filterCategory = "All";
    std::string sortCol;  // "" = null
    bool sortReverse = false;
};

static UiState loadState(const std::string &path) {
    UiState s;
    auto doc = jsonReadFile(path);
    if (!doc || doc->type != JsonValue::Object) return s;
    auto str = [&](const char *key, const std::string &fallback) {
        const JsonValue *v = doc->find(key);
        return (v && v->type == JsonValue::String) ? v->str : fallback;
    };
    s.geometry = str("geometry", "");
    s.filterDone = str("filter_done", "all");
    s.filterPriority = str("filter_priority", "All");
    s.filterCategory = str("filter_category", "All");
    s.sortCol = str("sort_col", "");  // JSON null → not a string → ""
    const JsonValue *rev = doc->find("sort_reverse");
    s.sortReverse = rev && jsonTruthy(*rev);
    return s;
}

static bool saveState(const std::string &path, const UiState &s) {
    JsonValue doc = JsonValue::makeObject();  // same key order as the Python dict literal
    doc.set("geometry", JsonValue::makeString(s.geometry));
    doc.set("filter_done", JsonValue::makeString(s.filterDone));
    doc.set("filter_priority", JsonValue::makeString(s.filterPriority));
    doc.set("filter_category", JsonValue::makeString(s.filterCategory));
    doc.set("sort_col", s.sortCol.empty() ? JsonValue::makeNull() : JsonValue::makeString(s.sortCol));
    doc.set("sort_reverse", JsonValue::makeBool(s.sortReverse));
    return writeFileBytes(path, dumpsPython(doc));
}

// Tk geometry string "WxH+X+Y" (offsets may be negative: "+-1050+70").
struct Geometry { long w, h, x, y; };

static std::optional<Geometry> parseGeometry(const std::string &g) {
    const char *p = g.c_str();
    char *end;
    Geometry r;
    r.w = strtol(p, &end, 10);
    if (end == p || *end != 'x') return std::nullopt;
    p = end + 1;
    r.h = strtol(p, &end, 10);
    if (end == p || (*end != '+' && *end != '-')) return std::nullopt;
    // Tk writes "+X" / "+-X" / "-X"; strtol after skipping one '+' handles all
    auto offset = [&](long *out) {
        if (*end == '+') end++;
        p = end;
        *out = strtol(p, &end, 10);
        return end != p;
    };
    if (!offset(&r.x)) return std::nullopt;
    if (*end != '+' && *end != '-') return std::nullopt;
    if (!offset(&r.y)) return std::nullopt;
    return *end == '\0' ? std::optional<Geometry>(r) : std::nullopt;
}

static std::string formatGeometry(long w, long h, long x, long y) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%ldx%ld+%ld+%ld", w, h, x, y);
    return buf;
}

// ── Globals (computed once in wWinMain, injected by tests) ────────────

static std::string gBaseDir;   // dirname of the executable (== repo root)
static std::string gDataFile;  // resolved todos.json
static std::string gStateFile; // per-machine todo_state.json beside the executable

#if !defined(TODOLIST_TESTING)

#include <commctrl.h>
#include <windowsx.h>

static std::string executableDir() {
    wchar_t buf[4096];
    DWORD n = GetModuleFileNameW(nullptr, buf, 4096);
    if (n == 0 || n >= 4096) return ".";
    return dirName(narrow(std::wstring(buf, n)));
}

// ── Win32 UI ──────────────────────────────────────────────────────────

#define CLR_LIST_BG    RGB(0xD6, 0xEB, 0xFF)  // Tk "Todo.Treeview" background
#define CLR_HEADER     RGB(0xFF, 0xFF, 0xB3)  // Tk heading background
#define CLR_HEADER_HOT RGB(0xFF, 0xFF, 0x88)  // Tk heading pressed ("active")
#define CLR_DONE_FG    RGB(0x88, 0x88, 0x88)  // "done" tag
#define CLR_HIGH_FG    RGB(0xCC, 0x00, 0x00)  // "high" tag
#define CLR_OVERDUE_BG RGB(0xFF, 0xE0, 0xE0)  // "overdue" tag

enum {
    IDC_ENTRY_TEXT = 100, IDC_ENTRY_PRIORITY, IDC_ENTRY_CATEGORY, IDC_ENTRY_DUE, IDC_BTN_ADD,
    IDC_RADIO_ALL, IDC_RADIO_ACTIVE, IDC_RADIO_DONE, IDC_FILTER_PRI, IDC_FILTER_CAT,
    IDC_BTN_TOGGLE, IDC_BTN_EDIT, IDC_BTN_DELETE, IDC_BTN_UP, IDC_BTN_DOWN, IDC_LIST,
    IDC_E_TEXT, IDC_E_PRI, IDC_E_CAT, IDC_E_DUE, IDC_E_SAVE,
};
static const UINT_PTR TIMER_POLL = 1;

static const char *COLUMN_IDS[] = {"done", "text", "priority", "category", "due", "created"};

struct EditDialogState {
    HWND dlg = nullptr, eText = nullptr, ePri = nullptr, eCat = nullptr, eDue = nullptr;
    bool saved = false, closed = false;
};

struct App {
    HWND win = nullptr;
    HWND groupNew, lblTask, entryText, lblPri, entryPriority, lblCat, entryCategory, lblDue,
        entryDue, dueHint, btnAdd;
    HWND lblShow, radioAll, radioActive, radioDone, sep1, lblFPri, filterPri, lblFCat, filterCat,
        sep2, btnToggle, btnEdit, btnDelete, btnUp, btnDown, statusLabel;
    HWND list = nullptr, header = nullptr;
    HFONT font = nullptr;
    UINT dpi = 96;

    std::vector<JsonValue> todos;
    std::vector<std::string> categories{"General", "Work", "Personal", "Errands"};
    std::vector<size_t> visible;  // tree position -> index in todos
    std::string filterDone = "all", filterPriority = "All", filterCategory = "All", sortCol;
    bool sortReverse = false;
    Mtime dataMtime;
    bool modalOpen = false;   // edit dialog open — pause the sync auto-reload
    int dialogDepth = 0;      // any alert up — poll paused, like NSAlert's modal runloop
    bool stateSaved = false;  // close/quit both save; only the first wins
    bool geometryApplied = false;
    bool inGeometryApply = false;
    Geometry pendingGeometry{0, 0, 0, 0};
    EditDialogState edit;
};
static App gApp;

static int S(int v) { return MulDiv(v, (int)gApp.dpi, 96); }

static std::wstring getTextW(HWND h) {
    int n = GetWindowTextLengthW(h);
    if (n <= 0) return L"";
    std::wstring w((size_t)n, L'\0');
    GetWindowTextW(h, &w[0], n + 1);
    return w;
}

static std::string getTextUtf8(HWND h) { return narrow(getTextW(h)); }

// ── Alerts (tkinter messagebox analogs) — the poll pauses while one is up ──

static void alertWarn(HWND owner, const wchar_t *title, const wchar_t *msg) {
    gApp.dialogDepth++;
    MessageBoxW(owner, msg, title, MB_OK | MB_ICONWARNING);
    gApp.dialogDepth--;
}

static void alertInfo(HWND owner, const wchar_t *title, const wchar_t *msg) {
    gApp.dialogDepth++;
    MessageBoxW(owner, msg, title, MB_OK | MB_ICONINFORMATION);
    gApp.dialogDepth--;
}

static bool alertYesNo(HWND owner, const wchar_t *title, const wchar_t *msg) {
    gApp.dialogDepth++;
    int r = MessageBoxW(owner, msg, title, MB_YESNO | MB_ICONWARNING);
    gApp.dialogDepth--;
    return r == IDYES;
}

// ── Fonts / DPI ──

static HFONT makeUiFont(UINT dpi) {
    NONCLIENTMETRICSW ncm{};
    ncm.cbSize = sizeof(ncm);
    if (SystemParametersInfoForDpi(SPI_GETNONCLIENTMETRICS, sizeof(ncm), &ncm, 0, dpi))
        return CreateFontIndirectW(&ncm.lfMessageFont);
    LOGFONTW lf{};
    lf.lfHeight = -MulDiv(9, (int)dpi, 72);
    wcscpy_s(lf.lfFaceName, L"Segoe UI");
    return CreateFontIndirectW(&lf);
}

static BOOL CALLBACK setFontProc(HWND h, LPARAM lp) {
    SendMessageW(h, WM_SETFONT, (WPARAM)lp, TRUE);
    return TRUE;
}

static void setFonts(HWND root) { EnumChildWindows(root, setFontProc, (LPARAM)gApp.font); }

// ── Combo helpers ──

static void comboAdd(HWND combo, const std::string &s) {
    SendMessageW(combo, CB_ADDSTRING, 0, (LPARAM)widen(s).c_str());
}

static std::string comboSelectedText(HWND combo, const char *fallback) {
    int sel = (int)SendMessageW(combo, CB_GETCURSEL, 0, 0);
    if (sel < 0) return fallback;
    int len = (int)SendMessageW(combo, CB_GETLBTEXTLEN, sel, 0);
    if (len <= 0) return fallback;
    std::wstring w((size_t)len, L'\0');
    SendMessageW(combo, CB_GETLBTEXT, sel, (LPARAM)&w[0]);
    return narrow(w);
}

static bool comboSelectExact(HWND combo, const std::string &s) {
    LRESULT i = SendMessageW(combo, CB_FINDSTRINGEXACT, (WPARAM)-1, (LPARAM)widen(s).c_str());
    if (i == CB_ERR) return false;
    SendMessageW(combo, CB_SETCURSEL, (WPARAM)i, 0);
    return true;
}

static void updateCategoryCombos() {
    std::wstring current = getTextW(gApp.entryCategory);  // editable — preserve typed text
    SendMessageW(gApp.entryCategory, CB_RESETCONTENT, 0, 0);
    for (const auto &c : gApp.categories) comboAdd(gApp.entryCategory, c);
    SetWindowTextW(gApp.entryCategory, current.c_str());

    std::string sel = comboSelectedText(gApp.filterCat, "All");
    SendMessageW(gApp.filterCat, CB_RESETCONTENT, 0, 0);
    comboAdd(gApp.filterCat, "All");
    for (const auto &c : gApp.categories) comboAdd(gApp.filterCat, c);
    if (!comboSelectExact(gApp.filterCat, sel)) SendMessageW(gApp.filterCat, CB_SETCURSEL, 0, 0);
}

// ── Refresh (mirrors _refresh_tree: rebuild + selection cleared) ──

static void refreshTree() {
    auto rows = visibleRows(gApp.todos, gApp.filterDone, gApp.filterPriority, gApp.filterCategory,
                            gApp.sortCol, gApp.sortReverse);
    gApp.visible.clear();
    SendMessageW(gApp.list, WM_SETREDRAW, FALSE, 0);
    ListView_DeleteAllItems(gApp.list);  // Tk delete+reinsert loses the selection; so do we
    int i = 0;
    for (const auto &r : rows) {
        const JsonValue &t = gApp.todos[r.realIndex];
        std::wstring cols[6] = {todoDone(t) ? L"✓" : L"", widen(todoStr(t, "text")),
                                widen(todoStr(t, "priority")), widen(todoStr(t, "category")),
                                widen(todoStr(t, "due")), widen(todoStr(t, "created"))};
        LVITEMW item{};
        item.mask = LVIF_TEXT;
        item.iItem = i;
        item.pszText = &cols[0][0];
        ListView_InsertItem(gApp.list, &item);
        for (int c = 1; c < 6; c++) ListView_SetItemText(gApp.list, i, c, &cols[c][0]);
        gApp.visible.push_back(r.realIndex);
        i++;
    }
    SendMessageW(gApp.list, WM_SETREDRAW, TRUE, 0);
    InvalidateRect(gApp.list, nullptr, TRUE);

    size_t total = gApp.todos.size(), done = 0;
    for (const auto &t : gApp.todos)
        if (todoDone(t)) done++;
    wchar_t buf[128];
    swprintf(buf, 128, L"%zu active, %zu done, %zu total", total - done, done, total);
    SetWindowTextW(gApp.statusLabel, buf);
}

// ── Selection helpers ──

static int selectedRealIndex(bool require) {
    int row = ListView_GetNextItem(gApp.list, -1, LVNI_SELECTED);
    int idx = -1;
    if (row >= 0 && (size_t)row < gApp.visible.size()) idx = (int)gApp.visible[(size_t)row];
    if (idx < 0 && require) alertInfo(gApp.win, L"No selection", L"Select a task first.");
    return idx;
}

static void selectRow(int row) {
    ListView_SetItemState(gApp.list, row, LVIS_SELECTED | LVIS_FOCUSED,
                          LVIS_SELECTED | LVIS_FOCUSED);
    ListView_EnsureVisible(gApp.list, row, FALSE);
}

static void selectRealIndex(size_t realIdx) {
    for (size_t i = 0; i < gApp.visible.size(); i++)
        if (gApp.visible[i] == realIdx) { selectRow((int)i); return; }
}

// ── Persistence ──

static void loadDataFromDisk() {
    loadData(gDataFile, gApp.todos, gApp.categories, &gApp.dataMtime);
    updateCategoryCombos();
}

static void saveDataUi() {
    // Another machine may have synced a newer file since this window last
    // read it — ask before silently overwriting that work
    Mtime disk = statMtime(gDataFile);
    if (disk != gApp.dataMtime) {
        bool keep = alertYesNo(gApp.win, L"Sync conflict",
                               L"todos.json changed on disk (edited on another computer?) "
                               L"since this window loaded it.\n\n"
                               L"Yes = save anyway, overwriting the other version\n"
                               L"No = discard this change and load the newer file");
        if (!keep) {
            loadDataFromDisk();
            refreshTree();
            return;
        }
    }
    writeDataFile(gDataFile, gApp.todos, gApp.categories, &gApp.dataMtime);
}

static void pollExternalChange() {
    if (gApp.modalOpen || gApp.dialogDepth > 0) return;  // timer repeats — reschedule implicit
    Mtime m = statMtime(gDataFile);
    if (m != gApp.dataMtime) {
        loadDataFromDisk();
        refreshTree();
    }
    if (absorbForks(gDataFile, gApp.todos, gApp.categories)) {
        updateCategoryCombos();
        refreshTree();
        saveDataUi();
    }
}

// ── Data operations ──

static void addTodo() {
    std::string text = stripped(getTextUtf8(gApp.entryText));
    if (text.empty()) return;

    std::string category = stripped(getTextUtf8(gApp.entryCategory));
    if (category.empty()) category = "General";
    // NOTE: like the Python, a new category is registered BEFORE date
    // validation — an invalid date still leaves the category added
    if (std::find(gApp.categories.begin(), gApp.categories.end(), category) ==
        gApp.categories.end()) {
        gApp.categories.push_back(category);
        updateCategoryCombos();
    }

    std::string due = stripped(getTextUtf8(gApp.entryDue));
    if (!due.empty() && !parseDate(due)) {
        alertWarn(gApp.win, L"Invalid date", L"Use DD/MM/YYYY format for due date.");
        return;
    }

    JsonValue todo = JsonValue::makeObject();  // key order = the Python dict literal
    todo.set("text", JsonValue::makeString(text));
    todo.set("priority", JsonValue::makeString(comboSelectedText(gApp.entryPriority, "Medium")));
    todo.set("category", JsonValue::makeString(category));
    todo.set("due", JsonValue::makeString(due));
    todo.set("done", JsonValue::makeBool(false));
    todo.set("created", JsonValue::makeString(nowCreatedString()));
    gApp.todos.push_back(std::move(todo));
    SetWindowTextW(gApp.entryText, L"");
    SetWindowTextW(gApp.entryDue, L"");
    refreshTree();
    saveDataUi();
    // Select the last visible row (the Python selects children[-1] as-is,
    // even when sorting/filtering means that row isn't the new todo)
    if (!gApp.visible.empty()) selectRow((int)gApp.visible.size() - 1);
}

static void toggleDone() {
    int idx = selectedRealIndex(false);
    if (idx < 0) return;
    JsonValue &todo = gApp.todos[(size_t)idx];
    todo.set("done", JsonValue::makeBool(!todoDone(todo)));
    refreshTree();
    saveDataUi();
}

static void deleteTodo() {
    int idx = selectedRealIndex(true);
    if (idx < 0) return;
    gApp.todos.erase(gApp.todos.begin() + idx);
    refreshTree();
    saveDataUi();
}

static void moveUp() {
    int idx = selectedRealIndex(false);
    if (idx <= 0) return;
    std::swap(gApp.todos[(size_t)idx], gApp.todos[(size_t)idx - 1]);
    refreshTree();
    saveDataUi();
    selectRealIndex((size_t)(idx - 1));
}

static void moveDown() {
    int idx = selectedRealIndex(false);
    if (idx < 0 || (size_t)idx + 1 >= gApp.todos.size()) return;
    std::swap(gApp.todos[(size_t)idx], gApp.todos[(size_t)idx + 1]);
    refreshTree();
    saveDataUi();
    selectRealIndex((size_t)(idx + 1));
}

// ── Filters ──

static void filterChanged() {
    bool active = Button_GetCheck(gApp.radioActive) == BST_CHECKED;
    bool completed = Button_GetCheck(gApp.radioDone) == BST_CHECKED;
    gApp.filterDone = active ? "active" : completed ? "completed" : "all";
    gApp.filterPriority = comboSelectedText(gApp.filterPri, "All");
    gApp.filterCategory = comboSelectedText(gApp.filterCat, "All");
    refreshTree();
}

static void headingClick(const std::string &col) {
    if (gApp.sortCol == col) gApp.sortReverse = !gApp.sortReverse;
    else { gApp.sortCol = col; gApp.sortReverse = false; }
    refreshTree();
}

// ── Edit dialog (modal Toplevel analog; pauses the sync poll) ──

static LRESULT CALLBACK EditDlgProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_COMMAND:
            if (LOWORD(wp) == IDC_E_SAVE && HIWORD(wp) == BN_CLICKED) {
                // Validate, then end the modal loop with "saved". Validation
                // alerts run nested-modal, like the Tk dialog's parented boxes.
                std::string newText = stripped(getTextUtf8(gApp.edit.eText));
                if (newText.empty()) {
                    alertWarn(h, L"Empty", L"Task text cannot be empty.");
                    return 0;
                }
                std::string newDue = stripped(getTextUtf8(gApp.edit.eDue));
                if (!newDue.empty() && !parseDate(newDue)) {
                    alertWarn(h, L"Invalid date", L"Use DD/MM/YYYY format.");
                    return 0;
                }
                gApp.edit.saved = true;
                gApp.edit.closed = true;
                return 0;
            }
            break;
        case WM_CLOSE:  // X on the dialog — abandon edits, like the Tk Toplevel
            gApp.edit.closed = true;
            return 0;
        case WM_CTLCOLORSTATIC:
            SetBkMode((HDC)wp, TRANSPARENT);
            return (LRESULT)GetSysColorBrush(COLOR_BTNFACE);
    }
    return DefWindowProcW(h, msg, wp, lp);
}

static void editTodo() {
    int idx = selectedRealIndex(true);
    if (idx < 0) return;

    HINSTANCE inst = GetModuleHandleW(nullptr);
    gApp.edit = EditDialogState{};
    RECT dr{0, 0, S(420), S(220)};
    AdjustWindowRectExForDpi(&dr, WS_POPUP | WS_CAPTION | WS_SYSMENU, FALSE, 0, gApp.dpi);
    RECT mr;
    GetWindowRect(gApp.win, &mr);
    int dw = dr.right - dr.left, dh = dr.bottom - dr.top;
    int dx = mr.left + ((mr.right - mr.left) - dw) / 2;
    int dy = mr.top + ((mr.bottom - mr.top) - dh) / 2;
    HWND dlg = CreateWindowExW(WS_EX_DLGMODALFRAME, L"TodoNativeEditDlg", L"Edit Task",
                               WS_POPUP | WS_CAPTION | WS_SYSMENU, dx, dy, dw, dh, gApp.win,
                               nullptr, inst, nullptr);
    gApp.edit.dlg = dlg;

    auto lbl = [&](const wchar_t *t, int y) {
        CreateWindowExW(0, L"STATIC", t, WS_CHILD | WS_VISIBLE | SS_LEFT, S(16), y + S(3), S(126),
                        S(18), dlg, nullptr, inst, nullptr);
    };
    lbl(L"Task:", S(16));
    lbl(L"Priority:", S(54));
    lbl(L"Category:", S(92));
    lbl(L"Due (DD/MM/YYYY):", S(130));

    const JsonValue &todo = gApp.todos[(size_t)idx];
    gApp.edit.eText = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", widen(todoStr(todo, "text")).c_str(),
                                      WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL, S(150),
                                      S(16), S(250), S(23), dlg, (HMENU)IDC_E_TEXT, inst, nullptr);
    gApp.edit.ePri = CreateWindowExW(0, L"COMBOBOX", L"",
                                     WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_VSCROLL |
                                         CBS_DROPDOWNLIST,
                                     S(150), S(54), S(110), S(200), dlg, (HMENU)IDC_E_PRI, inst,
                                     nullptr);
    for (const char *p : {"High", "Medium", "Low"}) comboAdd(gApp.edit.ePri, p);
    if (!comboSelectExact(gApp.edit.ePri, todoStr(todo, "priority")))
        SendMessageW(gApp.edit.ePri, CB_SETCURSEL, (WPARAM)-1, 0);
    gApp.edit.eCat = CreateWindowExW(0, L"COMBOBOX", L"",
                                     WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWN,
                                     S(150), S(92), S(160), S(200), dlg, (HMENU)IDC_E_CAT, inst,
                                     nullptr);
    for (const auto &c : gApp.categories) comboAdd(gApp.edit.eCat, c);
    SetWindowTextW(gApp.edit.eCat, widen(todoStr(todo, "category")).c_str());
    gApp.edit.eDue = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", widen(todoStr(todo, "due")).c_str(),
                                     WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL, S(150),
                                     S(130), S(110), S(23), dlg, (HMENU)IDC_E_DUE, inst, nullptr);
    CreateWindowExW(0, L"BUTTON", L"Save", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
                    S(165), S(172), S(90), S(28), dlg, (HMENU)IDC_E_SAVE, inst, nullptr);
    setFonts(dlg);

    gApp.modalOpen = true;  // a synced reload would shift indexes under the editor
    EnableWindow(gApp.win, FALSE);
    ShowWindow(dlg, SW_SHOW);
    SetFocus(gApp.edit.eText);

    MSG msg;
    while (!gApp.edit.closed) {
        BOOL r = GetMessageW(&msg, nullptr, 0, 0);
        if (r == 0) {  // WM_QUIT — repost for the outer loop and bail
            PostQuitMessage((int)msg.wParam);
            break;
        }
        if (r == -1) break;
        if (!IsDialogMessageW(dlg, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }

    bool saved = gApp.edit.saved;
    std::string newText = stripped(getTextUtf8(gApp.edit.eText));
    std::string newPri = comboSelectedText(gApp.edit.ePri, "Medium");
    std::string newCat = stripped(getTextUtf8(gApp.edit.eCat));
    std::string newDue = stripped(getTextUtf8(gApp.edit.eDue));

    EnableWindow(gApp.win, TRUE);  // before destroy, or Windows activates another app
    SetActiveWindow(gApp.win);
    DestroyWindow(dlg);
    gApp.modalOpen = false;
    gApp.edit = EditDialogState{};

    if (saved) {
        if (newCat.empty()) newCat = "General";
        if (std::find(gApp.categories.begin(), gApp.categories.end(), newCat) ==
            gApp.categories.end()) {
            gApp.categories.push_back(newCat);
            updateCategoryCombos();
        }
        JsonValue &t = gApp.todos[(size_t)idx];
        t.set("text", JsonValue::makeString(newText));
        t.set("priority", JsonValue::makeString(newPri));
        t.set("category", JsonValue::makeString(newCat));
        t.set("due", JsonValue::makeString(newDue));
        refreshTree();
        saveDataUi();
    }
}

// ── Window state ──

static void applyGeometry(const std::string &geo) {
    auto g = parseGeometry(geo);
    if (!g) return;
    gApp.geometryApplied = true;
    gApp.pendingGeometry = *g;
    gApp.inGeometryApply = true;  // WM_DPICHANGED during the move re-asserts our rect
    RECT r{0, 0, (LONG)g->w, (LONG)g->h};
    AdjustWindowRectExForDpi(&r, (DWORD)GetWindowLongW(gApp.win, GWL_STYLE), FALSE,
                             (DWORD)GetWindowLongW(gApp.win, GWL_EXSTYLE), gApp.dpi);
    SetWindowPos(gApp.win, nullptr, (int)g->x, (int)g->y, r.right - r.left, r.bottom - r.top,
                 SWP_NOZORDER | SWP_NOACTIVATE);
    gApp.inGeometryApply = false;
}

// Tk-style geometry: WxH = client (content) size, +X+Y = outer top-left.
static std::string currentGeometry() {
    RECT wr, cr;
    GetWindowRect(gApp.win, &wr);
    GetClientRect(gApp.win, &cr);
    return formatGeometry(cr.right, cr.bottom, wr.left, wr.top);
}

static void loadUiState() {
    UiState s = loadState(gStateFile);
    if (!s.geometry.empty()) applyGeometry(s.geometry);
    gApp.filterDone = s.filterDone;
    gApp.filterPriority = s.filterPriority;
    gApp.filterCategory = s.filterCategory;
    gApp.sortCol = s.sortCol;
    gApp.sortReverse = s.sortReverse;
    Button_SetCheck(gApp.radioAll, gApp.filterDone == "all" ? BST_CHECKED : BST_UNCHECKED);
    Button_SetCheck(gApp.radioActive, gApp.filterDone == "active" ? BST_CHECKED : BST_UNCHECKED);
    Button_SetCheck(gApp.radioDone, gApp.filterDone == "completed" ? BST_CHECKED : BST_UNCHECKED);
    comboSelectExact(gApp.filterPri, gApp.filterPriority);
    comboSelectExact(gApp.filterCat, gApp.filterCategory);
}

static void saveUiState() {
    if (gApp.stateSaved) return;
    gApp.stateSaved = true;
    UiState s;
    s.geometry = currentGeometry();
    s.filterDone = gApp.filterDone;
    s.filterPriority = gApp.filterPriority;
    s.filterCategory = gApp.filterCategory;
    s.sortCol = gApp.sortCol;
    s.sortReverse = gApp.sortReverse;
    saveState(gStateFile, s);
}

// ── Layout (cursor-based, everything scaled by the window's DPI) ──

static void moveCtl(HWND h, int x, int y, int w, int hgt) {
    MoveWindow(h, x, y, w, hgt, TRUE);
}

static void layout() {
    RECT rc;
    GetClientRect(gApp.win, &rc);
    int W = rc.right, H = rc.bottom, m = S(8);

    // "New Task" group box row
    moveCtl(gApp.groupNew, m, S(2), W - 2 * m, S(54));
    int y = S(24), hE = S(23), hL = S(18), hB = S(24);
    int x = m + S(10);
    moveCtl(gApp.lblTask, x, y + S(3), S(32), hL);            x += S(34);
    moveCtl(gApp.entryText, x, y, S(250), hE);                x += S(258);
    moveCtl(gApp.lblPri, x, y + S(3), S(46), hL);             x += S(48);
    moveCtl(gApp.entryPriority, x, y, S(76), S(200));         x += S(84);
    moveCtl(gApp.lblCat, x, y + S(3), S(54), hL);             x += S(56);
    moveCtl(gApp.entryCategory, x, y, S(106), S(200));        x += S(114);
    moveCtl(gApp.lblDue, x, y + S(3), S(28), hL);             x += S(30);
    moveCtl(gApp.entryDue, x, y, S(76), hE);                  x += S(80);
    moveCtl(gApp.dueHint, x, y + S(3), S(82), hL);            x += S(86);
    moveCtl(gApp.btnAdd, x, y - S(1), S(48), hB);

    // Filter / action row
    int y2 = S(62);
    x = m;
    moveCtl(gApp.lblShow, x, y2 + S(4), S(36), hL);           x += S(38);
    moveCtl(gApp.radioAll, x, y2 + S(2), S(38), S(20));       x += S(40);
    moveCtl(gApp.radioActive, x, y2 + S(2), S(54), S(20));    x += S(56);
    moveCtl(gApp.radioDone, x, y2 + S(2), S(50), S(20));      x += S(56);
    moveCtl(gApp.sep1, x, y2 + S(1), S(2), S(22));            x += S(10);
    moveCtl(gApp.lblFPri, x, y2 + S(4), S(46), hL);           x += S(48);
    moveCtl(gApp.filterPri, x, y2 + S(1), S(76), S(200));     x += S(82);
    moveCtl(gApp.lblFCat, x, y2 + S(4), S(54), hL);           x += S(56);
    moveCtl(gApp.filterCat, x, y2 + S(1), S(106), S(200));    x += S(114);
    moveCtl(gApp.sep2, x, y2 + S(1), S(2), S(22));            x += S(10);
    moveCtl(gApp.btnToggle, x, y2, S(80), hB);                x += S(84);
    moveCtl(gApp.btnEdit, x, y2, S(40), hB);                  x += S(44);
    moveCtl(gApp.btnDelete, x, y2, S(50), hB);                x += S(54);
    moveCtl(gApp.btnUp, x, y2, S(62), hB);                    x += S(66);
    moveCtl(gApp.btnDown, x, y2, S(78), hB);                  x += S(82);
    int statusW = W - m - x;
    if (statusW < 0) statusW = 0;
    moveCtl(gApp.statusLabel, x, y2 + S(4), statusW, hL);

    // Table fills the rest
    int listY = S(92);
    moveCtl(gApp.list, m, listY, W - 2 * m, H - listY - m);
}

// ── ListView subclass: yellow header via custom draw (the header sends its
// NM_CUSTOMDRAW to its parent — the ListView — so we intercept it there) ──

static LRESULT CALLBACK listSubclassProc(HWND h, UINT msg, WPARAM wp, LPARAM lp, UINT_PTR,
                                         DWORD_PTR) {
    if (msg == WM_NOTIFY) {
        NMHDR *nm = (NMHDR *)lp;
        if (nm->hwndFrom == gApp.header && nm->code == NM_CUSTOMDRAW) {
            NMCUSTOMDRAW *cd = (NMCUSTOMDRAW *)lp;
            if (cd->dwDrawStage == CDDS_PREPAINT) return CDRF_NOTIFYITEMDRAW;
            if (cd->dwDrawStage == CDDS_ITEMPREPAINT) {
                bool pressed = (cd->uItemState & CDIS_SELECTED) != 0;
                HBRUSH fill = CreateSolidBrush(pressed ? CLR_HEADER_HOT : CLR_HEADER);
                FillRect(cd->hdc, &cd->rc, fill);
                DeleteObject(fill);
                HBRUSH grey = CreateSolidBrush(RGB(0x99, 0x99, 0x99));
                RECT rr = cd->rc; rr.left = rr.right - 1;
                FillRect(cd->hdc, &rr, grey);
                RECT rb = cd->rc; rb.top = rb.bottom - 1;
                FillRect(cd->hdc, &rb, grey);
                DeleteObject(grey);
                wchar_t buf[128] = L"";
                HDITEMW hi{};
                hi.mask = HDI_TEXT;
                hi.pszText = buf;
                hi.cchTextMax = 127;
                Header_GetItem(gApp.header, (int)cd->dwItemSpec, &hi);
                RECT tr = cd->rc;
                tr.left += S(6);
                tr.right -= S(2);
                SetBkMode(cd->hdc, TRANSPARENT);
                SetTextColor(cd->hdc, RGB(0, 0, 0));
                HFONT old = (HFONT)SelectObject(cd->hdc, gApp.font);
                DrawTextW(cd->hdc, buf, -1, &tr,
                          DT_SINGLELINE | DT_VCENTER | DT_LEFT | DT_END_ELLIPSIS | DT_NOPREFIX);
                SelectObject(cd->hdc, old);
                return CDRF_SKIPDEFAULT;
            }
            return CDRF_DODEFAULT;
        }
    }
    return DefSubclassProc(h, msg, wp, lp);
}

// ── UI construction ──

static HWND mkChild(const wchar_t *cls, const wchar_t *text, DWORD style, DWORD ex, int id) {
    return CreateWindowExW(ex, cls, text, WS_CHILD | WS_VISIBLE | style, 0, 0, 10, 10, gApp.win,
                           (HMENU)(INT_PTR)id, GetModuleHandleW(nullptr), nullptr);
}

static void buildUI() {
    gApp.groupNew = mkChild(L"BUTTON", L"New Task", BS_GROUPBOX, 0, 0);
    gApp.lblTask = mkChild(L"STATIC", L"Task:", SS_LEFT, 0, 0);
    gApp.entryText = mkChild(L"EDIT", L"", WS_TABSTOP | ES_AUTOHSCROLL, WS_EX_CLIENTEDGE,
                             IDC_ENTRY_TEXT);
    gApp.lblPri = mkChild(L"STATIC", L"Priority:", SS_LEFT, 0, 0);
    gApp.entryPriority = mkChild(L"COMBOBOX", L"", WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWNLIST, 0,
                                 IDC_ENTRY_PRIORITY);
    for (const char *p : {"High", "Medium", "Low"}) comboAdd(gApp.entryPriority, p);
    comboSelectExact(gApp.entryPriority, "Medium");
    gApp.lblCat = mkChild(L"STATIC", L"Category:", SS_LEFT, 0, 0);
    gApp.entryCategory = mkChild(L"COMBOBOX", L"", WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWN, 0,
                                 IDC_ENTRY_CATEGORY);
    for (const auto &c : gApp.categories) comboAdd(gApp.entryCategory, c);
    SetWindowTextW(gApp.entryCategory, L"General");
    gApp.lblDue = mkChild(L"STATIC", L"Due:", SS_LEFT, 0, 0);
    gApp.entryDue = mkChild(L"EDIT", L"", WS_TABSTOP | ES_AUTOHSCROLL, WS_EX_CLIENTEDGE,
                            IDC_ENTRY_DUE);
    gApp.dueHint = mkChild(L"STATIC", L"(DD/MM/YYYY)", SS_LEFT, 0, 0);
    gApp.btnAdd = mkChild(L"BUTTON", L"Add", WS_TABSTOP | BS_PUSHBUTTON, 0, IDC_BTN_ADD);

    gApp.lblShow = mkChild(L"STATIC", L"Show:", SS_LEFT, 0, 0);
    gApp.radioAll = mkChild(L"BUTTON", L"All", WS_TABSTOP | WS_GROUP | BS_AUTORADIOBUTTON, 0,
                            IDC_RADIO_ALL);
    gApp.radioActive = mkChild(L"BUTTON", L"Active", BS_AUTORADIOBUTTON, 0, IDC_RADIO_ACTIVE);
    gApp.radioDone = mkChild(L"BUTTON", L"Done", BS_AUTORADIOBUTTON, 0, IDC_RADIO_DONE);
    Button_SetCheck(gApp.radioAll, BST_CHECKED);
    gApp.sep1 = mkChild(L"STATIC", L"", SS_ETCHEDVERT, 0, 0);
    gApp.lblFPri = mkChild(L"STATIC", L"Priority:", SS_LEFT, 0, 0);
    gApp.filterPri = mkChild(L"COMBOBOX", L"",
                             WS_TABSTOP | WS_GROUP | WS_VSCROLL | CBS_DROPDOWNLIST, 0,
                             IDC_FILTER_PRI);
    for (const char *p : {"All", "High", "Medium", "Low"}) comboAdd(gApp.filterPri, p);
    SendMessageW(gApp.filterPri, CB_SETCURSEL, 0, 0);
    gApp.lblFCat = mkChild(L"STATIC", L"Category:", SS_LEFT, 0, 0);
    gApp.filterCat = mkChild(L"COMBOBOX", L"", WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWNLIST, 0,
                             IDC_FILTER_CAT);
    comboAdd(gApp.filterCat, "All");
    for (const auto &c : gApp.categories) comboAdd(gApp.filterCat, c);
    SendMessageW(gApp.filterCat, CB_SETCURSEL, 0, 0);
    gApp.sep2 = mkChild(L"STATIC", L"", SS_ETCHEDVERT, 0, 0);
    gApp.btnToggle = mkChild(L"BUTTON", L"Toggle Done", WS_TABSTOP | BS_PUSHBUTTON, 0,
                             IDC_BTN_TOGGLE);
    gApp.btnEdit = mkChild(L"BUTTON", L"Edit", WS_TABSTOP | BS_PUSHBUTTON, 0, IDC_BTN_EDIT);
    gApp.btnDelete = mkChild(L"BUTTON", L"Delete", WS_TABSTOP | BS_PUSHBUTTON, 0, IDC_BTN_DELETE);
    gApp.btnUp = mkChild(L"BUTTON", L"Move Up", WS_TABSTOP | BS_PUSHBUTTON, 0, IDC_BTN_UP);
    gApp.btnDown = mkChild(L"BUTTON", L"Move Down", WS_TABSTOP | BS_PUSHBUTTON, 0, IDC_BTN_DOWN);
    gApp.statusLabel = mkChild(L"STATIC", L"", SS_RIGHT, 0, 0);

    gApp.list = mkChild(WC_LISTVIEWW, L"",
                        WS_TABSTOP | LVS_REPORT | LVS_SINGLESEL | LVS_SHOWSELALWAYS,
                        WS_EX_CLIENTEDGE, IDC_LIST);
    ListView_SetExtendedListViewStyle(gApp.list, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
    ListView_SetBkColor(gApp.list, CLR_LIST_BG);
    ListView_SetTextBkColor(gApp.list, CLR_LIST_BG);
    ListView_SetTextColor(gApp.list, RGB(0, 0, 0));

    struct ColSpec { const wchar_t *title; int width; };
    const ColSpec cols[] = {{L"Done", 50},     {L"Task", 300},    {L"Priority", 70},
                            {L"Category", 90}, {L"Due Date", 90}, {L"Created", 90}};
    for (int i = 0; i < 6; i++) {
        LVCOLUMNW col{};
        col.mask = LVCF_TEXT | LVCF_WIDTH | LVCF_SUBITEM;
        col.pszText = (LPWSTR)cols[i].title;
        col.cx = S(cols[i].width);
        col.iSubItem = i;
        ListView_InsertColumn(gApp.list, i, &col);
    }
    gApp.header = ListView_GetHeader(gApp.list);
    SetWindowSubclass(gApp.list, listSubclassProc, 1, 0);

    setFonts(gApp.win);
    layout();
}

// ── Main window proc ──

static LRESULT CALLBACK mainWndProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_CREATE:
            gApp.win = h;
            gApp.dpi = GetDpiForWindow(h);
            gApp.font = makeUiFont(gApp.dpi);
            buildUI();
            return 0;

        case WM_SIZE:
            if (gApp.list) layout();
            return 0;

        case WM_GETMINMAXINFO: {
            UINT dpi = gApp.win ? gApp.dpi : GetDpiForSystem();
            RECT r{0, 0, MulDiv(700, (int)dpi, 96), MulDiv(400, (int)dpi, 96)};
            AdjustWindowRectExForDpi(&r, WS_OVERLAPPEDWINDOW, FALSE, 0, dpi);
            MINMAXINFO *mmi = (MINMAXINFO *)lp;
            mmi->ptMinTrackSize.x = r.right - r.left;
            mmi->ptMinTrackSize.y = r.bottom - r.top;
            return 0;
        }

        case WM_DPICHANGED: {
            gApp.dpi = HIWORD(wp);
            HFONT old = gApp.font;
            gApp.font = makeUiFont(gApp.dpi);
            setFonts(h);
            if (old) DeleteObject(old);
            if (gApp.inGeometryApply) {
                // Re-assert the saved rect at the new DPI: the system's
                // suggested rect would rescale the restored size and make
                // the geometry round-trip drift on every launch.
                const Geometry &g = gApp.pendingGeometry;
                RECT r{0, 0, (LONG)g.w, (LONG)g.h};
                AdjustWindowRectExForDpi(&r, (DWORD)GetWindowLongW(h, GWL_STYLE), FALSE,
                                         (DWORD)GetWindowLongW(h, GWL_EXSTYLE), gApp.dpi);
                SetWindowPos(h, nullptr, (int)g.x, (int)g.y, r.right - r.left, r.bottom - r.top,
                             SWP_NOZORDER | SWP_NOACTIVATE);
            } else {
                RECT *sug = (RECT *)lp;
                SetWindowPos(h, nullptr, sug->left, sug->top, sug->right - sug->left,
                             sug->bottom - sug->top, SWP_NOZORDER | SWP_NOACTIVATE);
            }
            layout();
            return 0;
        }

        case WM_CTLCOLORSTATIC: {
            HDC dc = (HDC)wp;
            SetBkMode(dc, TRANSPARENT);
            if ((HWND)lp == gApp.dueHint) SetTextColor(dc, GetSysColor(COLOR_GRAYTEXT));
            return (LRESULT)GetSysColorBrush(COLOR_BTNFACE);
        }

        case WM_COMMAND: {
            int id = LOWORD(wp), code = HIWORD(wp);
            if (code == BN_CLICKED) {
                switch (id) {
                    case IDC_BTN_ADD: addTodo(); return 0;
                    case IDC_RADIO_ALL:
                    case IDC_RADIO_ACTIVE:
                    case IDC_RADIO_DONE: filterChanged(); return 0;
                    case IDC_BTN_TOGGLE: toggleDone(); return 0;
                    case IDC_BTN_EDIT: editTodo(); return 0;
                    case IDC_BTN_DELETE: deleteTodo(); return 0;
                    case IDC_BTN_UP: moveUp(); return 0;
                    case IDC_BTN_DOWN: moveDown(); return 0;
                }
            }
            if (code == CBN_SELCHANGE && (id == IDC_FILTER_PRI || id == IDC_FILTER_CAT)) {
                filterChanged();
                return 0;
            }
            break;
        }

        case WM_NOTIFY: {
            NMHDR *nm = (NMHDR *)lp;
            if (nm->hwndFrom == gApp.list) {
                switch (nm->code) {
                    case LVN_COLUMNCLICK: {
                        int c = ((NMLISTVIEW *)nm)->iSubItem;
                        if (c >= 0 && c < 6) headingClick(COLUMN_IDS[c]);
                        return 0;
                    }
                    case NM_DBLCLK:
                        toggleDone();  // Tk <Double-1> acted on the selection, wherever clicked
                        return 0;
                    case NM_CUSTOMDRAW: {
                        NMLVCUSTOMDRAW *cd = (NMLVCUSTOMDRAW *)lp;
                        if (cd->nmcd.dwDrawStage == CDDS_PREPAINT) return CDRF_NOTIFYITEMDRAW;
                        if (cd->nmcd.dwDrawStage == CDDS_ITEMPREPAINT) {
                            size_t row = (size_t)cd->nmcd.dwItemSpec;
                            if (row < gApp.visible.size()) {
                                const JsonValue &t = gApp.todos[gApp.visible[row]];
                                bool done = todoDone(t);
                                cd->clrText = done ? CLR_DONE_FG
                                              : todoStr(t, "priority") == "High" ? CLR_HIGH_FG
                                                                                 : RGB(0, 0, 0);
                                bool overdue = false;  // "overdue" tag
                                if (!done) {
                                    auto d = parseDate(todoStr(t, "due"));
                                    if (d && *d < todayYmd()) overdue = true;
                                }
                                cd->clrTextBk = overdue ? CLR_OVERDUE_BG : CLR_LIST_BG;
                            }
                            return CDRF_DODEFAULT;
                        }
                        return CDRF_DODEFAULT;
                    }
                }
            }
            break;
        }

        case WM_TIMER:
            if (wp == TIMER_POLL) {
                pollExternalChange();
                return 0;
            }
            break;

        // Deliberately NO data save on close: every action already saves
        // immediately, so a close-save could only rewrite todos.json with
        // state that is identical or STALE — overwriting another machine's
        // synced write and forking its copy (observed live 2026-07-18 with
        // the Python version).
        case WM_CLOSE:
            saveUiState();
            DestroyWindow(h);
            return 0;

        case WM_ENDSESSION:  // logoff/shutdown — same state save as close
            if (wp) saveUiState();
            return 0;

        case WM_DESTROY:
            KillTimer(h, TIMER_POLL);
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

// ── wWinMain ──────────────────────────────────────────────────────────

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int) {
    // The manifest declares PerMonitorV2; this is the fallback for a build
    // that skipped the .rc (harmless no-op once the context is already set).
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    gBaseDir = executableDir();
    gStateFile = pathJoin(gBaseDir, "todo_state.json");
    gDataFile = resolveDataFile(gBaseDir, getEnvVar, getEnvVar("TODOLIST_DATA_DIR"));

    INITCOMMONCONTROLSEX icc{sizeof(icc), ICC_LISTVIEW_CLASSES | ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&icc);

    HICON icon = LoadIconW(inst, MAKEINTRESOURCEW(1));  // embedded via TodoList.rc
    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = mainWndProc;
    wc.hInstance = inst;
    wc.hIcon = icon ? icon : LoadIconW(nullptr, IDI_APPLICATION);
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.lpszClassName = L"TodoNativeMain";
    RegisterClassExW(&wc);

    WNDCLASSEXW dc{};
    dc.cbSize = sizeof(dc);
    dc.lpfnWndProc = EditDlgProc;
    dc.hInstance = inst;
    dc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    dc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    dc.lpszClassName = L"TodoNativeEditDlg";
    RegisterClassExW(&dc);

    // The title shows at a glance whether this machine is on the shared file
    bool synced = dirName(gDataFile) != gBaseDir;
    HWND win = CreateWindowExW(0, L"TodoNativeMain",
                               synced ? L"Todo List — synced" : L"Todo List",
                               WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT, 100, 100,
                               nullptr, nullptr, inst, nullptr);
    if (!win) return 1;

    loadDataFromDisk();
    if (absorbForks(gDataFile, gApp.todos, gApp.categories)) {  // heal forks at startup
        updateCategoryCombos();
        saveDataUi();
    }
    loadUiState();
    refreshTree();
    SetTimer(win, TIMER_POLL, DATA_POLL_MS, nullptr);

    if (!gApp.geometryApplied) {  // no saved geometry: default 900x550, centered
        RECT r{0, 0, S(900), S(550)};
        AdjustWindowRectExForDpi(&r, WS_OVERLAPPEDWINDOW, FALSE, 0, gApp.dpi);
        int w = r.right - r.left, hgt = r.bottom - r.top;
        RECT wa{0, 0, 800, 600};
        SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
        SetWindowPos(win, nullptr, wa.left + ((wa.right - wa.left) - w) / 2,
                     wa.top + ((wa.bottom - wa.top) - hgt) / 2, w, hgt,
                     SWP_NOZORDER | SWP_NOACTIVATE);
    }

    ShowWindow(win, SW_SHOW);
    UpdateWindow(win);

    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0)) {
        // Return in the task entry adds, like the Tk <Return> binding
        if (msg.message == WM_KEYDOWN && msg.wParam == VK_RETURN && msg.hwnd == gApp.entryText) {
            addTodo();
            continue;
        }
        if (gApp.win && IsWindowEnabled(gApp.win) && IsDialogMessageW(gApp.win, &msg))
            continue;  // Tab navigation between controls, like Tk
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return (int)msg.wParam;
}

#endif  // !TODOLIST_TESTING
