// TodoList.mm — native C++/Cocoa port of TodoList.py (functionality-identical).
//
// Build:  ./build_todolist_native.sh   (produces ./TodoList.exe, an arm64 Mach-O)
//
// The data layer is Foundation-only C++ (testable headless — see
// tests/test_todolist_native.mm, which #includes this file with
// TODOLIST_TESTING defined); the UI layer is AppKit. JSON goes through
// NSJSONSerialization so the app round-trips the exact files Python's json
// module writes — other machines still run TodoList.py against the same
// OneDrive-synced todos.json, so both implementations must interoperate.
//
// todos.json lives in <OneDrive>/MyAppShare (TODOLIST_DATA_DIR override,
// script-dir fallback), same resolution rules as TodoList.py including the
// one-way fold of the legacy <OneDrive>/TodoList dir. todo_state.json
// (geometry/filters/sort) is per-machine, shared with the Python version —
// same schema, same location (the executable's directory).

#import <Foundation/Foundation.h>
#if !defined(TODOLIST_TESTING)
#import <Cocoa/Cocoa.h>
#endif

#include <dirent.h>
#include <fnmatch.h>
#include <mach-o/dyld.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <optional>
#include <string>
#include <utility>
#include <vector>

// ── Filesystem helpers ────────────────────────────────────────────────

static std::string pathJoin(const std::string &a, const std::string &b) {
    if (a.empty()) return b;
    if (a.back() == '/') return a + b;
    return a + "/" + b;
}

static std::string dirName(const std::string &p) {
    auto pos = p.find_last_of('/');
    if (pos == std::string::npos) return "";
    if (pos == 0) return "/";
    return p.substr(0, pos);
}

static std::string baseName(const std::string &p) {
    auto pos = p.find_last_of('/');
    return pos == std::string::npos ? p : p.substr(pos + 1);
}

static bool fileExists(const std::string &p) {
    struct stat st;
    return stat(p.c_str(), &st) == 0;
}

static bool isDir(const std::string &p) {
    struct stat st;
    return stat(p.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
}

static bool makeDirs(const std::string &p) {  // mkdir -p semantics
    if (p.empty()) return false;
    NSError *err = nil;
    BOOL ok = [[NSFileManager defaultManager]
        createDirectoryAtPath:[NSString stringWithUTF8String:p.c_str()]
        withIntermediateDirectories:YES attributes:nil error:&err];
    return ok;
}

// mtime as (sec, nsec) — full stat precision, the analog of os.path.getmtime.
// nullopt = file absent / unstattable, matching Python's None sentinel.
typedef std::optional<std::pair<long long, long>> Mtime;

static Mtime statMtime(const std::string &p) {
    struct stat st;
    if (stat(p.c_str(), &st) != 0) return std::nullopt;
    return std::make_pair((long long)st.st_mtimespec.tv_sec, st.st_mtimespec.tv_nsec);
}

static std::vector<std::string> listDir(const std::string &dir) {
    std::vector<std::string> names;
    DIR *d = opendir(dir.c_str());
    if (!d) return names;
    while (struct dirent *e = readdir(d)) {
        std::string n = e->d_name;
        if (n != "." && n != "..") names.push_back(n);
    }
    closedir(d);
    return names;
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
static bool splitThree(const std::string &s, char sep, int maxA, int maxB, int maxC,
                       long *a, long *b, long *c) {
    size_t p1 = s.find(sep);
    if (p1 == std::string::npos) return false;
    size_t p2 = s.find(sep, p1 + 1);
    if (p2 == std::string::npos || s.find(sep, p2 + 1) != std::string::npos) return false;
    std::string fa = s.substr(0, p1), fb = s.substr(p1 + 1, p2 - p1 - 1), fc = s.substr(p2 + 1);
    auto digits = [](const std::string &f, size_t maxLen) {
        if (f.empty() || f.size() > maxLen) return false;
        for (char ch : f) if (ch < '0' || ch > '9') return false;
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
        return Ymd{(int)c, (int)b, (int)a};              // %d/%m/%Y
    if (splitThree(s, '-', 4, 2, 2, &a, &b, &c) && validYmd((int)a, (int)b, (int)c))
        return Ymd{(int)a, (int)b, (int)c};              // %Y-%m-%d
    if (splitThree(s, '/', 2, 2, 4, &a, &b, &c) && validYmd((int)c, (int)a, (int)b))
        return Ymd{(int)c, (int)a, (int)b};              // %m/%d/%Y
    return std::nullopt;
}

static Ymd todayYmd() {
    time_t t = time(nullptr);
    struct tm lt;
    localtime_r(&t, &lt);
    return Ymd{lt.tm_year + 1900, lt.tm_mon + 1, lt.tm_mday};
}

static std::string nowCreatedString() {  // strftime("%d/%m/%Y")
    time_t t = time(nullptr);
    struct tm lt;
    localtime_r(&t, &lt);
    char buf[16];
    snprintf(buf, sizeof(buf), "%02d/%02d/%04d", lt.tm_mday, lt.tm_mon + 1, lt.tm_year + 1900);
    return buf;
}

// ── Shared-data path resolution (mirrors TodoList.py exactly) ─────────

static const char *SHARED_SUBDIR = "MyAppShare";
static const char *LEGACY_SHARED_SUBDIR = "TodoList";  // pre-2026-07-19 home
static const int DATA_POLL_MS = 5000;

// macOS branch of _find_onedrive_root: ~/Library/CloudStorage/OneDrive-*
// (sorted, OneDrive-Personal preferred), legacy ~/OneDrive fallback.
static std::string findOneDriveRoot(const std::string &home) {
    std::string cloud = pathJoin(pathJoin(home, "Library"), "CloudStorage");
    std::vector<std::string> candidates;
    for (const auto &n : listDir(cloud))
        if (fnmatch("OneDrive-*", n.c_str(), 0) == 0)
            candidates.push_back(pathJoin(cloud, n));
    std::sort(candidates.begin(), candidates.end());
    for (const auto &c : candidates) {
        const std::string suffix = "OneDrive-Personal";
        if (c.size() >= suffix.size() && c.compare(c.size() - suffix.size(), suffix.size(), suffix) == 0)
            return c;
    }
    if (!candidates.empty()) return candidates[0];
    std::string legacy = pathJoin(home, "OneDrive");
    return isDir(legacy) ? legacy : "";
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
    if (rename(legacy.c_str(), target.c_str()) != 0) return;
    rmdir(dirName(legacy).c_str());  // best-effort, like the Python except-pass
}

static bool copyFileBytes(const std::string &src, const std::string &dst) {
    NSError *err = nil;
    [[NSFileManager defaultManager] removeItemAtPath:[NSString stringWithUTF8String:dst.c_str()]
                                               error:nil];
    return [[NSFileManager defaultManager]
        copyItemAtPath:[NSString stringWithUTF8String:src.c_str()]
                toPath:[NSString stringWithUTF8String:dst.c_str()] error:&err];
}

// _resolve_data_file: TODOLIST_DATA_DIR override, else <OneDrive>/MyAppShare
// (folding in the legacy dir), else the executable's directory. First run
// with an empty shared slot seeds it from the local file.
static std::string resolveDataFile(const std::string &baseDir, const std::string &home,
                                   const char *overrideDir) {
    std::string local = pathJoin(baseDir, "todos.json");
    std::string sharedDir = overrideDir ? overrideDir : "";
    std::string onedrive;
    if (sharedDir.empty()) {
        onedrive = findOneDriveRoot(home);
        if (onedrive.empty()) return local;
        sharedDir = pathJoin(onedrive, SHARED_SUBDIR);
    }
    if (!makeDirs(sharedDir)) return local;
    if (!onedrive.empty())  // not under an explicit override — fold in the old home
        migrateLegacyDir(onedrive, sharedDir);
    std::string shared = pathJoin(sharedDir, "todos.json");
    if (!fileExists(shared) && fileExists(local))
        copyFileBytes(local, shared);
    return shared;
}

// ── JSON model I/O ────────────────────────────────────────────────────
// todos: NSMutableArray of NSMutableDictionary — kept as dictionaries (not a
// C++ struct) so unknown keys written by a future TodoList.py survive a
// load→save round trip on this machine, exactly as Python's dicts do.

static NSString *nstr(const std::string &s) {
    NSString *r = [NSString stringWithUTF8String:s.c_str()];
    return r ? r : @"";
}

static std::string cstr(NSString *s) { return s ? std::string([s UTF8String]) : std::string(); }

static NSString *todoStr(NSDictionary *todo, NSString *key) {
    id v = todo[key];
    return [v isKindOfClass:[NSString class]] ? (NSString *)v : @"";
}

static bool todoDone(NSDictionary *todo) {
    id v = todo[@"done"];
    return [v respondsToSelector:@selector(boolValue)] && [v boolValue];
}

static id jsonRead(const std::string &path) {  // nil on any error
    NSData *data = [NSData dataWithContentsOfFile:nstr(path)];
    if (!data) return nil;
    return [NSJSONSerialization JSONObjectWithData:data
                                           options:NSJSONReadingMutableContainers
                                             error:nil];
}

enum class LoadResult { Loaded, Missing, Corrupt };

// _load_data contract: missing file → mtime None, keep in-memory todos;
// unreadable/corrupt (possibly a half-synced cloud write) → remember the
// mtime so the poll stops re-reading it, keep in-memory todos; success →
// adopt todos, adopt categories only when the saved list is non-empty.
static LoadResult loadData(const std::string &path, NSMutableArray *todos,
                           NSMutableArray *categories, Mtime *mtime) {
    if (!fileExists(path)) {
        *mtime = std::nullopt;
        return LoadResult::Missing;
    }
    id data = jsonRead(path);
    *mtime = statMtime(path);
    if (![data isKindOfClass:[NSDictionary class]]) return LoadResult::Corrupt;
    [todos removeAllObjects];
    id t = data[@"todos"];
    if ([t isKindOfClass:[NSArray class]])
        for (id item in (NSArray *)t)
            if ([item isKindOfClass:[NSDictionary class]])
                [todos addObject:[item mutableCopy]];
    id cats = data[@"categories"];
    if ([cats isKindOfClass:[NSArray class]] && [(NSArray *)cats count] > 0) {
        [categories removeAllObjects];
        for (id c in (NSArray *)cats)
            if ([c isKindOfClass:[NSString class]]) [categories addObject:c];
    }
    return LoadResult::Loaded;
}

// Atomic write via .tmp + rename, recreating the parent first — OneDrive
// garbage-collects a still-empty shared dir within seconds of creation
// (observed 2026-07-18), so the first save on a fresh machine may find its
// parent gone. Returns false best-effort, never throws.
static bool writeDataFile(const std::string &path, NSArray *todos, NSArray *categories,
                          Mtime *mtimeOut) {
    NSDictionary *doc = @{@"todos" : todos, @"categories" : categories};
    NSData *data = [NSJSONSerialization
        dataWithJSONObject:doc
                   options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys
                     error:nil];
    if (!data) return false;
    makeDirs(dirName(path));
    std::string tmp = path + ".tmp";
    if (![data writeToFile:nstr(tmp) atomically:NO]) return false;
    if (rename(tmp.c_str(), path.c_str()) != 0) {  // atomic — OneDrive never sees a half-write
        unlink(tmp.c_str());
        return false;
    }
    if (mtimeOut) *mtimeOut = statMtime(path);
    return true;
}

// Identity key for fork merging: (text, created), with "key absent" distinct
// from "empty string" exactly like Python's (None, "") tuples.
static NSString *todoIdentity(NSDictionary *todo) {
    id t = todo[@"text"], c = todo[@"created"];
    NSString *tk = [t isKindOfClass:[NSString class]]
        ? [@"P" stringByAppendingString:(NSString *)t] : @"M";
    NSString *ck = [c isKindOfClass:[NSString class]]
        ? [@"P" stringByAppendingString:(NSString *)c] : @"M";
    return [NSString stringWithFormat:@"%@\x1f%@", tk, ck];
}

// _absorb_conflict_forks: OneDrive resolves a concurrent write by renaming
// the losing machine's copy to todos-<ComputerName>.json beside the main
// file. Fold every fork's unique items back in (winner's version kept for
// items in both), union categories, delete the fork. Idempotent, so
// concurrent absorbers on different machines converge.
static bool absorbForks(const std::string &dataFile, NSMutableArray *todos,
                        NSMutableArray *categories) {
    std::string dir = dirName(dataFile);
    std::string base = baseName(dataFile);
    auto dot = base.rfind('.');
    if (dot != std::string::npos) base = base.substr(0, dot);
    std::string pattern = base + "-*.json";

    std::vector<std::string> forks;
    for (const auto &n : listDir(dir))
        if (fnmatch(pattern.c_str(), n.c_str(), 0) == 0)
            forks.push_back(pathJoin(dir, n));
    if (forks.empty()) return false;

    bool changed = false;
    NSMutableSet *seen = [NSMutableSet set];
    for (NSDictionary *t in todos) [seen addObject:todoIdentity(t)];

    for (const auto &fork : forks) {
        id data = jsonRead(fork);
        if (![data isKindOfClass:[NSDictionary class]])
            continue;  // unreadable / half-synced — retry next poll
        id ft = data[@"todos"];
        if ([ft isKindOfClass:[NSArray class]]) {
            for (id item in (NSArray *)ft) {
                if (![item isKindOfClass:[NSDictionary class]]) continue;
                NSString *key = todoIdentity(item);
                if (![seen containsObject:key]) {
                    [todos addObject:[item mutableCopy]];
                    [seen addObject:key];
                    changed = true;
                }
            }
        }
        id fc = data[@"categories"];
        if ([fc isKindOfClass:[NSArray class]]) {
            for (id c in (NSArray *)fc) {
                if ([c isKindOfClass:[NSString class]] && ![categories containsObject:c]) {
                    [categories addObject:c];
                    changed = true;
                }
            }
        }
        unlink(fork.c_str());  // the delete syncs, clearing the fork everywhere
    }
    return changed;
}

// ── Filtering & sorting (mirrors _refresh_tree / _sort_key) ───────────

static bool passesFilter(NSDictionary *todo, const std::string &filterDone,
                         const std::string &filterPriority, const std::string &filterCategory) {
    bool done = todoDone(todo);
    if (filterDone == "active" && done) return false;
    if (filterDone == "completed" && !done) return false;
    if (filterPriority != "All" && cstr(todoStr(todo, @"priority")) != filterPriority)
        return false;
    if (filterCategory != "All" && cstr(todoStr(todo, @"category")) != filterCategory)
        return false;
    return true;
}

static int priorityRank(NSDictionary *todo) {
    std::string p = cstr(todoStr(todo, @"priority"));
    if (p == "High") return 0;
    if (p == "Medium") return 1;
    if (p == "Low") return 2;
    return 9;
}

static Ymd dateRank(NSDictionary *todo, NSString *key) {
    auto d = parseDate(cstr(todoStr(todo, key)));
    return d ? *d : Ymd{10000, 0, 0};  // sorts after every valid date, like datetime.max
}

// strict-weak "a sorts before b" for one column, ascending
static bool todoLess(const std::string &col, NSDictionary *a, NSDictionary *b) {
    if (col == "done") return (todoDone(a) ? 0 : 1) < (todoDone(b) ? 0 : 1);
    if (col == "priority") return priorityRank(a) < priorityRank(b);
    if (col == "due" || col == "created") {
        NSString *key = col == "due" ? @"due" : @"created";
        return dateRank(a, key) < dateRank(b, key);
    }
    if (col == "text" || col == "category") {
        NSString *key = col == "text" ? @"text" : @"category";
        NSString *la = [todoStr(a, key) lowercaseString], *lb = [todoStr(b, key) lowercaseString];
        return [la compare:lb] == NSOrderedAscending;  // literal Unicode compare, like str <
    }
    return false;  // unknown column: todo.get(col, "") made every key equal in Python
}

struct VisibleRow {
    NSUInteger realIndex;
    NSDictionary *todo;
};

// Filter + optional sort. std::stable_sort with the reversed comparator
// reproduces Python's list.sort(key=..., reverse=...) exactly, including
// original-order preservation among equal keys in both directions.
static std::vector<VisibleRow> visibleRows(NSArray *todos, const std::string &filterDone,
                                           const std::string &filterPriority,
                                           const std::string &filterCategory,
                                           const std::string &sortCol, bool sortReverse) {
    std::vector<VisibleRow> rows;
    for (NSUInteger i = 0; i < [todos count]; i++) {
        NSDictionary *t = todos[i];
        if (passesFilter(t, filterDone, filterPriority, filterCategory))
            rows.push_back({i, t});
    }
    if (!sortCol.empty()) {
        std::stable_sort(rows.begin(), rows.end(),
                         [&](const VisibleRow &x, const VisibleRow &y) {
                             return sortReverse ? todoLess(sortCol, y.todo, x.todo)
                                                : todoLess(sortCol, x.todo, y.todo);
                         });
    }
    return rows;
}

// ── Per-machine UI state (todo_state.json — same schema as the .py) ───

struct UiState {
    std::string geometry;         // Tk-style "WxH+X+Y" ("" = none saved)
    std::string filterDone = "all";
    std::string filterPriority = "All";
    std::string filterCategory = "All";
    std::string sortCol;          // "" = null
    bool sortReverse = false;
};

static UiState loadState(const std::string &path) {
    UiState s;
    id data = jsonRead(path);
    if (![data isKindOfClass:[NSDictionary class]]) return s;
    NSDictionary *d = data;
    auto str = [&](NSString *key, const std::string &fallback) {
        id v = d[key];
        return [v isKindOfClass:[NSString class]] ? cstr(v) : fallback;
    };
    s.geometry = str(@"geometry", "");
    s.filterDone = str(@"filter_done", "all");
    s.filterPriority = str(@"filter_priority", "All");
    s.filterCategory = str(@"filter_category", "All");
    s.sortCol = str(@"sort_col", "");  // JSON null → not a string → ""
    id rev = d[@"sort_reverse"];
    s.sortReverse = [rev respondsToSelector:@selector(boolValue)] && [rev boolValue];
    return s;
}

static bool saveState(const std::string &path, const UiState &s) {
    NSDictionary *doc = @{
        @"geometry" : nstr(s.geometry),
        @"filter_done" : nstr(s.filterDone),
        @"filter_priority" : nstr(s.filterPriority),
        @"filter_category" : nstr(s.filterCategory),
        @"sort_col" : s.sortCol.empty() ? (id)[NSNull null] : (id)nstr(s.sortCol),
        @"sort_reverse" : s.sortReverse ? @YES : @NO,
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:doc
                                                   options:NSJSONWritingPrettyPrinted
                                                     error:nil];
    return data && [data writeToFile:nstr(path) atomically:YES];
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

// ── Globals (computed once in main, injected by tests) ────────────────

static std::string gBaseDir;   // dirname of the resolved executable (== repo root)
static std::string gDataFile;  // resolved todos.json
static std::string gStateFile; // per-machine todo_state.json beside the executable

#if !defined(TODOLIST_TESTING)

static std::string executableDir() {
    char buf[4096];
    uint32_t size = sizeof(buf);
    if (_NSGetExecutablePath(buf, &size) != 0) return ".";
    char real[PATH_MAX];
    if (!realpath(buf, real)) return dirName(buf);
    return dirName(real);
}

// ── AppKit UI ─────────────────────────────────────────────────────────

static NSColor *hexColor(unsigned rgb) {
    return [NSColor colorWithSRGBRed:((rgb >> 16) & 0xFF) / 255.0
                               green:((rgb >> 8) & 0xFF) / 255.0
                                blue:(rgb & 0xFF) / 255.0
                               alpha:1.0];
}

// Yellow header like the Tk "Todo.Treeview.Heading" style (#FFFFB3, #FFFF88
// while pressed).
@interface TodoHeaderCell : NSTableHeaderCell
@end
@implementation TodoHeaderCell
- (void)drawWithFrame:(NSRect)cellFrame inView:(NSView *)controlView {
    [(self.isHighlighted ? hexColor(0xFFFF88) : hexColor(0xFFFFB3)) setFill];
    NSRectFill(cellFrame);
    [[NSColor colorWithWhite:0.6 alpha:1.0] setFill];
    NSRectFill(NSMakeRect(NSMaxX(cellFrame) - 1, NSMinY(cellFrame), 1, NSHeight(cellFrame)));
    NSRectFill(NSMakeRect(NSMinX(cellFrame), NSMaxY(cellFrame) - 1, NSWidth(cellFrame), 1));
    [self drawInteriorWithFrame:cellFrame inView:controlView];
}
@end

@interface TodoApp : NSObject <NSApplicationDelegate, NSWindowDelegate,
                               NSTableViewDataSource, NSTableViewDelegate>
@end

@implementation TodoApp {
    NSWindow *_window;
    NSTextField *_entryText, *_entryDue, *_statusLabel;
    NSPopUpButton *_entryPriority, *_filterPriPopup, *_filterCatPopup;
    NSComboBox *_entryCategory;
    NSButton *_radioAll, *_radioActive, *_radioDone;
    NSTableView *_table;
    NSTimer *_pollTimer;

    NSMutableArray<NSMutableDictionary *> *_todos;
    NSMutableArray<NSString *> *_categories;
    std::vector<VisibleRow> _visible;
    std::string _filterDone, _filterPriority, _filterCategory, _sortCol;
    bool _sortReverse;
    Mtime _dataMtime;
    bool _modalOpen;   // edit dialog open — pause the sync auto-reload
    bool _stateSaved;  // close/quit both save; only the first wins
    bool _geometryApplied;
    NSDictionary *_editCtx;  // widgets of the (single) open edit dialog
}

- (instancetype)init {
    if ((self = [super init])) {
        _todos = [NSMutableArray array];
        _categories =
            [NSMutableArray arrayWithObjects:@"General", @"Work", @"Personal", @"Errands", nil];
        _filterDone = "all";
        _filterPriority = "All";
        _filterCategory = "All";
        _sortReverse = false;
        _modalOpen = false;
        _stateSaved = false;
    }
    return self;
}

// ── Alerts (tkinter messagebox analogs) ──

- (void)warn:(NSString *)title message:(NSString *)msg {
    NSAlert *a = [[NSAlert alloc] init];
    a.alertStyle = NSAlertStyleWarning;
    a.messageText = title;
    a.informativeText = msg;
    [a runModal];
}

- (BOOL)askYesNo:(NSString *)title message:(NSString *)msg {
    NSAlert *a = [[NSAlert alloc] init];
    a.alertStyle = NSAlertStyleWarning;
    a.messageText = title;
    a.informativeText = msg;
    [a addButtonWithTitle:@"Yes"];
    [a addButtonWithTitle:@"No"];
    return [a runModal] == NSAlertFirstButtonReturn;
}

// ── UI construction ──

- (NSTextField *)label:(NSString *)text {
    NSTextField *l = [NSTextField labelWithString:text];
    l.textColor = [NSColor blackColor];
    return l;
}

- (NSButton *)button:(NSString *)title action:(SEL)sel {
    NSButton *b = [NSButton buttonWithTitle:title target:self action:sel];
    b.bezelStyle = NSBezelStyleRounded;
    return b;
}

- (void)buildUI {
    bool synced = dirName(gDataFile) != gBaseDir;
    NSRect frame = NSMakeRect(0, 0, 900, 550);
    _window = [[NSWindow alloc]
        initWithContentRect:frame
                  styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                            NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
                    backing:NSBackingStoreBuffered
                      defer:NO];
    _window.title = synced ? @"Todo List — synced" : @"Todo List";
    _window.contentMinSize = NSMakeSize(700, 400);
    _window.delegate = self;
    _window.releasedWhenClosed = NO;

    NSView *content = _window.contentView;

    // ── "New Task" box ──
    NSBox *addBox = [[NSBox alloc] init];
    addBox.title = @"New Task";
    addBox.translatesAutoresizingMaskIntoConstraints = NO;

    _entryText = [[NSTextField alloc] init];
    [_entryText.widthAnchor constraintEqualToConstant:300].active = YES;
    _entryText.target = self;          // Return in the task entry adds, like
    _entryText.action = @selector(addTodo:);  // the Tk <Return> binding

    _entryPriority = [[NSPopUpButton alloc] init];
    [_entryPriority addItemsWithTitles:@[ @"High", @"Medium", @"Low" ]];
    [_entryPriority selectItemWithTitle:@"Medium"];

    _entryCategory = [[NSComboBox alloc] init];  // editable, like ttk.Combobox
    _entryCategory.completes = YES;
    [_entryCategory addItemsWithObjectValues:_categories];
    _entryCategory.stringValue = @"General";
    [_entryCategory.widthAnchor constraintEqualToConstant:120].active = YES;

    _entryDue = [[NSTextField alloc] init];
    [_entryDue.widthAnchor constraintEqualToConstant:90].active = YES;

    NSTextField *dueHint = [NSTextField labelWithString:@"(DD/MM/YYYY)"];
    dueHint.textColor = [NSColor grayColor];

    NSStackView *addRow = [NSStackView stackViewWithViews:@[
        [self label:@"Task:"], _entryText, [self label:@"Priority:"], _entryPriority,
        [self label:@"Category:"], _entryCategory, [self label:@"Due:"], _entryDue, dueHint,
        [self button:@"Add" action:@selector(addTodo:)]
    ]];
    addRow.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    addRow.spacing = 6;
    addRow.translatesAutoresizingMaskIntoConstraints = NO;
    addBox.contentView = [[NSView alloc] init];
    [addBox.contentView addSubview:addRow];
    NSLayoutConstraint *rowTrail = [addRow.trailingAnchor
        constraintLessThanOrEqualToAnchor:addBox.contentView.trailingAnchor
                                 constant:-4];
    rowTrail.priority = 400;  // clip, don't compress, when the window is narrow
    [NSLayoutConstraint activateConstraints:@[
        [addRow.leadingAnchor constraintEqualToAnchor:addBox.contentView.leadingAnchor constant:4],
        [addRow.topAnchor constraintEqualToAnchor:addBox.contentView.topAnchor constant:2],
        [addRow.bottomAnchor constraintEqualToAnchor:addBox.contentView.bottomAnchor constant:-2],
        rowTrail,
    ]];

    // ── Filter / action bar ──
    _radioAll = [NSButton radioButtonWithTitle:@"All" target:self action:@selector(filterChanged:)];
    _radioActive = [NSButton radioButtonWithTitle:@"Active"
                                           target:self
                                           action:@selector(filterChanged:)];
    _radioDone = [NSButton radioButtonWithTitle:@"Done"
                                         target:self
                                         action:@selector(filterChanged:)];
    _radioAll.state = NSControlStateValueOn;

    _filterPriPopup = [[NSPopUpButton alloc] init];
    [_filterPriPopup addItemsWithTitles:@[ @"All", @"High", @"Medium", @"Low" ]];
    _filterPriPopup.target = self;
    _filterPriPopup.action = @selector(filterChanged:);

    _filterCatPopup = [[NSPopUpButton alloc] init];
    _filterCatPopup.target = self;
    _filterCatPopup.action = @selector(filterChanged:);

    _statusLabel = [NSTextField labelWithString:@""];
    _statusLabel.alignment = NSTextAlignmentRight;
    [_statusLabel setContentHuggingPriority:NSLayoutPriorityDefaultLow - 10
                             forOrientation:NSLayoutConstraintOrientationHorizontal];

    auto vsep = [] {
        NSBox *b = [[NSBox alloc] init];
        b.boxType = NSBoxCustom;
        b.borderWidth = 0;
        b.fillColor = [NSColor colorWithWhite:0.65 alpha:1.0];
        [b.widthAnchor constraintEqualToConstant:1].active = YES;
        [b.heightAnchor constraintEqualToConstant:18].active = YES;
        return b;
    };

    NSStackView *filterRow = [NSStackView stackViewWithViews:@[
        [self label:@"Show:"], _radioAll, _radioActive, _radioDone, vsep(),
        [self label:@"Priority:"], _filterPriPopup, [self label:@"Category:"], _filterCatPopup,
        vsep(), [self button:@"Toggle Done" action:@selector(toggleDone:)],
        [self button:@"Edit" action:@selector(editTodo:)],
        [self button:@"Delete" action:@selector(deleteTodo:)],
        [self button:@"Move Up" action:@selector(moveUp:)],
        [self button:@"Move Down" action:@selector(moveDown:)], _statusLabel
    ]];
    filterRow.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    filterRow.spacing = 6;
    filterRow.translatesAutoresizingMaskIntoConstraints = NO;

    // ── Table ──
    _table = [[NSTableView alloc] init];
    _table.dataSource = self;
    _table.delegate = self;
    _table.rowHeight = 21;  // + default intercell spacing ≈ the Tk rowheight 24
    _table.backgroundColor = hexColor(0xD6EBFF);
    _table.usesAlternatingRowBackgroundColors = NO;
    _table.allowsMultipleSelection = NO;
    _table.allowsColumnReordering = NO;
    _table.target = self;
    _table.doubleAction = @selector(tableDoubleClicked:);
    _table.gridStyleMask = NSTableViewGridNone;

    struct ColSpec { const char *ident, *title; int width; };
    const ColSpec cols[] = {{"done", "Done", 50},      {"text", "Task", 300},
                            {"priority", "Priority", 70}, {"category", "Category", 90},
                            {"due", "Due Date", 90},   {"created", "Created", 90}};
    for (const auto &c : cols) {
        NSTableColumn *col =
            [[NSTableColumn alloc] initWithIdentifier:[NSString stringWithUTF8String:c.ident]];
        TodoHeaderCell *hc =
            [[TodoHeaderCell alloc] initTextCell:[NSString stringWithUTF8String:c.title]];
        hc.alignment = NSTextAlignmentLeft;
        col.headerCell = hc;
        col.width = c.width;
        col.minWidth = 40;
        col.resizingMask = NSTableColumnUserResizingMask;
        [_table addTableColumn:col];
    }
    NSTableColumn *last = _table.tableColumns.lastObject;
    last.resizingMask |= NSTableColumnAutoresizingMask;

    NSScrollView *scroll = [[NSScrollView alloc] init];
    scroll.documentView = _table;
    scroll.hasVerticalScroller = YES;
    scroll.drawsBackground = YES;
    scroll.backgroundColor = hexColor(0xD6EBFF);
    scroll.translatesAutoresizingMaskIntoConstraints = NO;

    [content addSubview:addBox];
    [content addSubview:filterRow];
    [content addSubview:scroll];
    // The two bars' trailing pins are LOW priority: like Tk pack, a narrow
    // window clips the row at its edge instead of the row's intrinsic width
    // dictating a minimum window width (required pins forced the window to
    // ~1144 and made the 900x550 default / 700 minimum unreachable).
    NSLayoutConstraint *addTrail =
        [addBox.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-6];
    addTrail.priority = 400;
    NSLayoutConstraint *filterTrail =
        [filterRow.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-10];
    filterTrail.priority = 400;
    [NSLayoutConstraint activateConstraints:@[
        [addBox.topAnchor constraintEqualToAnchor:content.topAnchor constant:6],
        [addBox.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:6],
        addTrail,
        [filterRow.topAnchor constraintEqualToAnchor:addBox.bottomAnchor constant:4],
        [filterRow.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:10],
        filterTrail,
        [scroll.topAnchor constraintEqualToAnchor:filterRow.bottomAnchor constant:4],
        [scroll.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:6],
        [scroll.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-6],
        [scroll.bottomAnchor constraintEqualToAnchor:content.bottomAnchor constant:-6],
    ]];

    [self updateCategoryCombos];
}

- (void)updateCategoryCombos {
    NSString *current = _entryCategory.stringValue;
    [_entryCategory removeAllItems];
    [_entryCategory addItemsWithObjectValues:_categories];
    _entryCategory.stringValue = current;

    NSString *sel = _filterCatPopup.titleOfSelectedItem ?: @"All";
    [_filterCatPopup removeAllItems];
    [_filterCatPopup addItemWithTitle:@"All"];
    for (NSString *c in _categories) [_filterCatPopup addItemWithTitle:c];
    if ([_filterCatPopup itemWithTitle:sel]) [_filterCatPopup selectItemWithTitle:sel];
}

// ── Table data / display ──

- (NSInteger)numberOfRowsInTableView:(NSTableView *)tv {
    return (NSInteger)_visible.size();
}

- (id)tableView:(NSTableView *)tv objectValueForTableColumn:(NSTableColumn *)col
            row:(NSInteger)row {
    if (row < 0 || (size_t)row >= _visible.size()) return @"";
    NSDictionary *todo = _visible[(size_t)row].todo;
    NSString *ident = col.identifier;
    if ([ident isEqualToString:@"done"]) return todoDone(todo) ? @"✓" : @"";
    return todoStr(todo, ident);
}

- (void)tableView:(NSTableView *)tv willDisplayCell:(id)cell
   forTableColumn:(NSTableColumn *)col row:(NSInteger)row {
    if (![cell isKindOfClass:[NSTextFieldCell class]]) return;
    NSTextFieldCell *c = cell;
    if (row < 0 || (size_t)row >= _visible.size()) return;
    NSDictionary *todo = _visible[(size_t)row].todo;
    bool done = todoDone(todo);

    if (done) c.textColor = hexColor(0x888888);                       // "done" tag
    else if (cstr(todoStr(todo, @"priority")) == "High") c.textColor = hexColor(0xCC0000);
    else c.textColor = [NSColor blackColor];

    bool overdue = false;                                             // "overdue" tag
    if (!done) {
        auto d = parseDate(cstr(todoStr(todo, @"due")));
        if (d && *d < todayYmd()) overdue = true;
    }
    bool selected = [tv isRowSelected:row];
    c.drawsBackground = overdue && !selected;
    if (c.drawsBackground) c.backgroundColor = hexColor(0xFFE0E0);
}

- (void)tableView:(NSTableView *)tv didClickTableColumn:(NSTableColumn *)col {
    std::string clicked = cstr(col.identifier);
    if (_sortCol == clicked) _sortReverse = !_sortReverse;
    else { _sortCol = clicked; _sortReverse = false; }
    [self refreshTree];
}

// ── Refresh (mirrors _refresh_tree: rebuild + selection cleared) ──

- (void)refreshTree {
    _visible = visibleRows(_todos, _filterDone, _filterPriority, _filterCategory, _sortCol,
                           _sortReverse);
    [_table deselectAll:nil];  // Tk delete+reinsert loses the selection; so do we
    [_table reloadData];

    NSUInteger total = _todos.count, done = 0;
    for (NSDictionary *t in _todos) if (todoDone(t)) done++;
    _statusLabel.stringValue = [NSString
        stringWithFormat:@"%lu active, %lu done, %lu total", total - done, done, total];
}

// ── Selection helpers ──

- (NSInteger)selectedRealIndexRequire:(BOOL)require {
    NSInteger row = _table.selectedRow;
    NSInteger idx = -1;
    if (row >= 0 && (size_t)row < _visible.size()) idx = (NSInteger)_visible[(size_t)row].realIndex;
    if (idx < 0 && require) {
        NSAlert *a = [[NSAlert alloc] init];
        a.alertStyle = NSAlertStyleInformational;
        a.messageText = @"No selection";
        a.informativeText = @"Select a task first.";
        [a runModal];
    }
    return idx;
}

- (void)selectRealIndex:(NSUInteger)realIdx {
    for (size_t i = 0; i < _visible.size(); i++) {
        if (_visible[i].realIndex == realIdx) {
            [_table selectRowIndexes:[NSIndexSet indexSetWithIndex:(NSUInteger)i]
                byExtendingSelection:NO];
            [_table scrollRowToVisible:(NSInteger)i];
            return;
        }
    }
}

// ── Data operations ──

- (void)addTodo:(id)sender {
    NSString *text =
        [_entryText.stringValue stringByTrimmingCharactersInSet:
            [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (text.length == 0) return;

    NSString *category =
        [_entryCategory.stringValue stringByTrimmingCharactersInSet:
            [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (category.length == 0) category = @"General";
    // NOTE: like the Python, a new category is registered BEFORE date
    // validation — an invalid date still leaves the category added
    if (![_categories containsObject:category]) {
        [_categories addObject:category];
        [self updateCategoryCombos];
    }

    NSString *due =
        [_entryDue.stringValue stringByTrimmingCharactersInSet:
            [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (due.length > 0 && !parseDate(cstr(due))) {
        [self warn:@"Invalid date" message:@"Use DD/MM/YYYY format for due date."];
        return;
    }

    NSMutableDictionary *todo = [@{
        @"text" : text,
        @"priority" : _entryPriority.titleOfSelectedItem ?: @"Medium",
        @"category" : category,
        @"due" : due,
        @"done" : @NO,
        @"created" : nstr(nowCreatedString()),
    } mutableCopy];
    [_todos addObject:todo];
    _entryText.stringValue = @"";
    _entryDue.stringValue = @"";
    [self refreshTree];
    [self saveData];
    // Select the last visible row (the Python selects children[-1] as-is,
    // even when sorting/filtering means that row isn't the new todo)
    if (!_visible.empty()) {
        NSInteger lastRow = (NSInteger)_visible.size() - 1;
        [_table selectRowIndexes:[NSIndexSet indexSetWithIndex:(NSUInteger)lastRow]
            byExtendingSelection:NO];
        [_table scrollRowToVisible:lastRow];
    }
}

- (void)toggleDone:(id)sender {
    NSInteger idx = [self selectedRealIndexRequire:NO];
    if (idx < 0) return;
    NSMutableDictionary *todo = _todos[(NSUInteger)idx];
    todo[@"done"] = todoDone(todo) ? @NO : @YES;
    [self refreshTree];
    [self saveData];
}

- (void)tableDoubleClicked:(id)sender {
    [self toggleDone:sender];  // Tk <Double-1> acted on the selection, wherever clicked
}

- (void)deleteTodo:(id)sender {
    NSInteger idx = [self selectedRealIndexRequire:YES];
    if (idx < 0) return;
    [_todos removeObjectAtIndex:(NSUInteger)idx];
    [self refreshTree];
    [self saveData];
}

- (void)moveUp:(id)sender {
    NSInteger idx = [self selectedRealIndexRequire:NO];
    if (idx <= 0) return;
    [_todos exchangeObjectAtIndex:(NSUInteger)idx withObjectAtIndex:(NSUInteger)idx - 1];
    [self refreshTree];
    [self saveData];
    [self selectRealIndex:(NSUInteger)(idx - 1)];
}

- (void)moveDown:(id)sender {
    NSInteger idx = [self selectedRealIndexRequire:NO];
    if (idx < 0 || (NSUInteger)idx >= _todos.count - 1 || _todos.count == 0) return;
    [_todos exchangeObjectAtIndex:(NSUInteger)idx withObjectAtIndex:(NSUInteger)idx + 1];
    [self refreshTree];
    [self saveData];
    [self selectRealIndex:(NSUInteger)(idx + 1)];
}

// ── Edit dialog (modal Toplevel analog; pauses the sync poll) ──

- (void)editTodo:(id)sender {
    NSInteger idx = [self selectedRealIndexRequire:YES];
    if (idx < 0) return;
    NSMutableDictionary *todo = _todos[(NSUInteger)idx];

    NSWindow *dlg = [[NSWindow alloc]
        initWithContentRect:NSMakeRect(0, 0, 420, 220)
                  styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                    backing:NSBackingStoreBuffered
                      defer:NO];
    dlg.title = @"Edit Task";
    dlg.releasedWhenClosed = NO;
    NSView *v = dlg.contentView;

    NSTextField *eText = [[NSTextField alloc] initWithFrame:NSMakeRect(150, 178, 250, 24)];
    eText.stringValue = todoStr(todo, @"text");
    NSPopUpButton *ePri = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(148, 138, 110, 26)];
    [ePri addItemsWithTitles:@[ @"High", @"Medium", @"Low" ]];
    NSString *pri = todoStr(todo, @"priority");
    if ([ePri itemWithTitle:pri]) [ePri selectItemWithTitle:pri];
    NSComboBox *eCat = [[NSComboBox alloc] initWithFrame:NSMakeRect(150, 100, 160, 26)];
    eCat.completes = YES;
    [eCat addItemsWithObjectValues:_categories];
    eCat.stringValue = todoStr(todo, @"category");
    NSTextField *eDue = [[NSTextField alloc] initWithFrame:NSMakeRect(150, 64, 110, 24)];
    eDue.stringValue = todoStr(todo, @"due");

    auto addLabel = [&](NSString *t, CGFloat y) {
        NSTextField *l = [NSTextField labelWithString:t];
        l.frame = NSMakeRect(16, y, 130, 20);
        [v addSubview:l];
    };
    addLabel(@"Task:", 180);
    addLabel(@"Priority:", 142);
    addLabel(@"Category:", 104);
    addLabel(@"Due (DD/MM/YYYY):", 66);
    [v addSubview:eText];
    [v addSubview:ePri];
    [v addSubview:eCat];
    [v addSubview:eDue];

    NSButton *saveBtn = [NSButton buttonWithTitle:@"Save"
                                           target:self
                                           action:@selector(editDialogSave:)];
    saveBtn.frame = NSMakeRect(170, 14, 90, 30);
    saveBtn.bezelStyle = NSBezelStyleRounded;
    [v addSubview:saveBtn];

    _editCtx = @{ @"dlg" : dlg, @"text" : eText, @"pri" : ePri, @"cat" : eCat, @"due" : eDue };
    dlg.delegate = self;  // X on the dialog must end the modal loop (see windowShouldClose)

    _modalOpen = true;  // a synced reload would shift indexes under the editor
    [dlg center];
    NSModalResponse resp = [NSApp runModalForWindow:dlg];
    _modalOpen = false;
    BOOL saved = (resp == NSModalResponseOK);
    [dlg orderOut:nil];

    if (saved) {
        NSString *newText = [eText.stringValue stringByTrimmingCharactersInSet:
            [NSCharacterSet whitespaceAndNewlineCharacterSet]];
        NSString *newDue = [eDue.stringValue stringByTrimmingCharactersInSet:
            [NSCharacterSet whitespaceAndNewlineCharacterSet]];
        NSString *newCat = [eCat.stringValue stringByTrimmingCharactersInSet:
            [NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (newCat.length == 0) newCat = @"General";
        if (![_categories containsObject:newCat]) {
            [_categories addObject:newCat];
            [self updateCategoryCombos];
        }
        todo[@"text"] = newText;
        todo[@"priority"] = ePri.titleOfSelectedItem ?: @"Medium";
        todo[@"category"] = newCat;
        todo[@"due"] = newDue;
        [self refreshTree];
        [self saveData];
    }
    _editCtx = nil;
}

// Save button inside the modal editor: validate, then end the modal loop
// with OK. Validation alerts run nested-modal, exactly like the Tk dialog's
// parented messageboxes.
- (void)editDialogSave:(id)sender {
    NSTextField *eText = _editCtx[@"text"];
    NSTextField *eDue = _editCtx[@"due"];
    NSString *newText = [eText.stringValue stringByTrimmingCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (newText.length == 0) {
        [self warn:@"Empty" message:@"Task text cannot be empty."];
        return;
    }
    NSString *newDue = [eDue.stringValue stringByTrimmingCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (newDue.length > 0 && !parseDate(cstr(newDue))) {
        [self warn:@"Invalid date" message:@"Use DD/MM/YYYY format."];
        return;
    }
    [NSApp stopModalWithCode:NSModalResponseOK];
}

// ── Filters ──

- (void)filterChanged:(id)sender {
    if (sender == _radioAll || sender == _radioActive || sender == _radioDone) {
        _radioAll.state = sender == _radioAll ? NSControlStateValueOn : NSControlStateValueOff;
        _radioActive.state =
            sender == _radioActive ? NSControlStateValueOn : NSControlStateValueOff;
        _radioDone.state = sender == _radioDone ? NSControlStateValueOn : NSControlStateValueOff;
    }
    _filterDone = _radioActive.state == NSControlStateValueOn  ? "active"
                  : _radioDone.state == NSControlStateValueOn ? "completed"
                                                              : "all";
    _filterPriority = cstr(_filterPriPopup.titleOfSelectedItem ?: @"All");
    _filterCategory = cstr(_filterCatPopup.titleOfSelectedItem ?: @"All");
    [self refreshTree];
}

// ── Persistence ──

- (void)loadDataFromDisk {
    loadData(gDataFile, _todos, _categories, &_dataMtime);
    [self updateCategoryCombos];
}

- (void)saveData {
    // Another machine may have synced a newer file since this window last
    // read it — ask before silently overwriting that work
    Mtime disk = statMtime(gDataFile);
    if (disk != _dataMtime) {
        BOOL keep = [self askYesNo:@"Sync conflict"
                           message:@"todos.json changed on disk (edited on another computer?) "
                                   @"since this window loaded it.\n\n"
                                   @"Yes = save anyway, overwriting the other version\n"
                                   @"No = discard this change and load the newer file"];
        if (!keep) {
            [self loadDataFromDisk];
            [self refreshTree];
            return;
        }
    }
    writeDataFile(gDataFile, _todos, _categories, &_dataMtime);
}

- (void)pollExternalChange:(NSTimer *)timer {
    if (_modalOpen) return;  // rescheduling is implicit — the timer repeats
    Mtime m = statMtime(gDataFile);
    if (m != _dataMtime) {
        [self loadDataFromDisk];
        [self refreshTree];
    }
    if (absorbForks(gDataFile, _todos, _categories)) {
        [self updateCategoryCombos];
        [self refreshTree];
        [self saveData];
    }
}

// ── Window state ──

- (void)applyGeometry:(const std::string &)geo {
    auto g = parseGeometry(geo);
    if (!g) return;
    _geometryApplied = true;
    [_window setContentSize:NSMakeSize(g->w, g->h)];
    // Tk offsets are from the primary screen's top-left, y downward; Cocoa's
    // origin is its bottom-left, y upward
    NSScreen *primary = [NSScreen screens].firstObject;
    CGFloat screenTop = NSMaxY(primary.frame);
    [_window setFrameTopLeftPoint:NSMakePoint(g->x, screenTop - g->y)];
}

- (std::string)currentGeometry {
    NSRect frame = _window.frame;
    NSRect content = [_window contentRectForFrameRect:frame];
    NSScreen *primary = [NSScreen screens].firstObject;
    CGFloat screenTop = NSMaxY(primary.frame);
    return formatGeometry((long)content.size.width, (long)content.size.height,
                          (long)frame.origin.x, (long)(screenTop - NSMaxY(frame)));
}

- (void)loadUiState {
    UiState s = loadState(gStateFile);
    if (!s.geometry.empty()) [self applyGeometry:s.geometry];
    _filterDone = s.filterDone;
    _filterPriority = s.filterPriority;
    _filterCategory = s.filterCategory;
    _sortCol = s.sortCol;
    _sortReverse = s.sortReverse;
    _radioAll.state = _filterDone == "all" ? NSControlStateValueOn : NSControlStateValueOff;
    _radioActive.state = _filterDone == "active" ? NSControlStateValueOn : NSControlStateValueOff;
    _radioDone.state = _filterDone == "completed" ? NSControlStateValueOn : NSControlStateValueOff;
    if ([_filterPriPopup itemWithTitle:nstr(_filterPriority)])
        [_filterPriPopup selectItemWithTitle:nstr(_filterPriority)];
    if ([_filterCatPopup itemWithTitle:nstr(_filterCategory)])
        [_filterCatPopup selectItemWithTitle:nstr(_filterCategory)];
}

- (void)saveUiState {
    if (_stateSaved) return;
    _stateSaved = true;
    UiState s;
    s.geometry = [self currentGeometry];
    s.filterDone = _filterDone;
    s.filterPriority = _filterPriority;
    s.filterCategory = _filterCategory;
    s.sortCol = _sortCol;
    s.sortReverse = _sortReverse;
    saveState(gStateFile, s);
}

// Deliberately NO data save on close: every action already saves
// immediately, so a close-save could only rewrite todos.json with state that
// is identical or STALE — overwriting another machine's synced write and
// forking its copy (observed live 2026-07-18 with the Python version).
- (BOOL)windowShouldClose:(NSWindow *)sender {
    if (sender != _window) {  // the edit dialog's X — abandon edits, like the Tk Toplevel
        [NSApp stopModalWithCode:NSModalResponseCancel];
        return YES;
    }
    [self saveUiState];
    [NSApp terminate:nil];
    return YES;
}

- (void)applicationWillTerminate:(NSNotification *)note {
    [self saveUiState];  // covers Cmd+Q; no-op if the close path already saved
}

// ── App lifecycle ──

- (void)applicationDidFinishLaunching:(NSNotification *)note {
    [self buildUI];
    [self loadDataFromDisk];
    if (absorbForks(gDataFile, _todos, _categories)) {  // heal forks at startup
        [self updateCategoryCombos];
        [self saveData];
    }
    [self loadUiState];
    [self refreshTree];

    _pollTimer = [NSTimer scheduledTimerWithTimeInterval:DATA_POLL_MS / 1000.0
                                                  target:self
                                                selector:@selector(pollExternalChange:)
                                                userInfo:nil
                                                 repeats:YES];

    if (!_geometryApplied) [_window center];
    [_window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}

@end

// ── main ──────────────────────────────────────────────────────────────

static void buildMenus(NSApplication *app) {
    NSMenu *menubar = [[NSMenu alloc] init];

    NSMenuItem *appItem = [[NSMenuItem alloc] init];
    NSMenu *appMenu = [[NSMenu alloc] init];
    [appMenu addItemWithTitle:@"Quit TodoList" action:@selector(terminate:) keyEquivalent:@"q"];
    appItem.submenu = appMenu;
    [menubar addItem:appItem];

    NSMenuItem *fileItem = [[NSMenuItem alloc] init];
    NSMenu *fileMenu = [[NSMenu alloc] initWithTitle:@"File"];
    [fileMenu addItemWithTitle:@"Close" action:@selector(performClose:) keyEquivalent:@"w"];
    fileItem.submenu = fileMenu;
    [menubar addItem:fileItem];

    NSMenuItem *editItem = [[NSMenuItem alloc] init];
    NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"Edit"];
    [editMenu addItemWithTitle:@"Undo" action:@selector(undo:) keyEquivalent:@"z"];
    [editMenu addItemWithTitle:@"Redo" action:@selector(redo:) keyEquivalent:@"Z"];
    [editMenu addItem:[NSMenuItem separatorItem]];
    [editMenu addItemWithTitle:@"Cut" action:@selector(cut:) keyEquivalent:@"x"];
    [editMenu addItemWithTitle:@"Copy" action:@selector(copy:) keyEquivalent:@"c"];
    [editMenu addItemWithTitle:@"Paste" action:@selector(paste:) keyEquivalent:@"v"];
    [editMenu addItemWithTitle:@"Select All" action:@selector(selectAll:) keyEquivalent:@"a"];
    editItem.submenu = editMenu;
    [menubar addItem:editItem];

    app.mainMenu = menubar;
}

int main(int, const char *[]) {
    @autoreleasepool {
        gBaseDir = executableDir();
        gStateFile = pathJoin(gBaseDir, "todo_state.json");
        const char *homeEnv = getenv("HOME");
        std::string home = homeEnv ? homeEnv : cstr(NSHomeDirectory());
        gDataFile = resolveDataFile(gBaseDir, home, getenv("TODOLIST_DATA_DIR"));

        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        buildMenus(app);

        // Dock icon: reuse the launcher artwork when the repo layout is intact
        for (const char *cand : {"desktop_launchers/icon_todolist_native_master.png",
                                 "desktop_launchers/icon_todolist_master.png"}) {
            std::string p = pathJoin(gBaseDir, cand);
            if (fileExists(p)) {
                NSImage *img = [[NSImage alloc] initWithContentsOfFile:nstr(p)];
                if (img) { app.applicationIconImage = img; break; }
            }
        }

        TodoApp *delegate = [[TodoApp alloc] init];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}

#endif  // !TODOLIST_TESTING
