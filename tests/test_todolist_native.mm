// Characterization tests for TodoList.exe's data core (tests/test_todolist_native.mm).
//
// Compiles TodoList.mm with TODOLIST_TESTING defined, which strips the AppKit
// layer — everything here runs headless against temp directories. Run via
// ./build_todolist_native.sh (which also does a Python-interop round-trip:
// the same todos.json must be readable and writable by BOTH TodoList.py and
// TodoList.exe, since OneDrive syncs one file between machines running
// either implementation).
//
// Extra mode for the build script:
//   test_bin roundtrip <in.json> <out.json>   # loadData -> writeDataFile

#define TODOLIST_TESTING 1
#include "../TodoList.mm"

static int gFailures = 0;
static int gChecks = 0;

#define CHECK(cond, name)                                                        \
    do {                                                                         \
        gChecks++;                                                               \
        if (!(cond)) {                                                           \
            gFailures++;                                                         \
            fprintf(stderr, "FAIL: %s (line %d)\n", name, __LINE__);             \
        }                                                                        \
    } while (0)

static std::string gTmpRoot;

static std::string tmpDir(const char *name) {
    std::string d = pathJoin(gTmpRoot, name);
    makeDirs(d);
    return d;
}

static void writeText(const std::string &path, const std::string &text) {
    makeDirs(dirName(path));
    FILE *f = fopen(path.c_str(), "wb");
    if (f) {
        fwrite(text.data(), 1, text.size(), f);
        fclose(f);
    }
}

static std::string readText(const std::string &path) {
    FILE *f = fopen(path.c_str(), "rb");
    if (!f) return "";
    std::string out;
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) out.append(buf, n);
    fclose(f);
    return out;
}

static NSMutableDictionary *mkTodo(const char *text, const char *pri, const char *cat,
                                   const char *due, bool done, const char *created) {
    return [@{
        @"text" : nstr(text), @"priority" : nstr(pri), @"category" : nstr(cat),
        @"due" : nstr(due), @"done" : done ? @YES : @NO, @"created" : nstr(created)
    } mutableCopy];
}

// ── parse_date parity ─────────────────────────────────────────────────

static void testParseDate() {
    auto d = parseDate("22/07/2026");
    CHECK(d && d->y == 2026 && d->m == 7 && d->d == 22, "parse DD/MM/YYYY");
    d = parseDate("1/2/2026");
    CHECK(d && d->y == 2026 && d->m == 2 && d->d == 1, "parse unpadded D/M/YYYY");
    d = parseDate("2026-07-22");
    CHECK(d && d->y == 2026 && d->m == 7 && d->d == 22, "parse ISO");
    d = parseDate("12/25/2026");  // month 25 fails fmt1 -> US fallback, like strptime cascade
    CHECK(d && d->y == 2026 && d->m == 12 && d->d == 25, "parse US fallback");
    d = parseDate("  22/07/2026  ");
    CHECK(d && d->d == 22, "strip whitespace");
    d = parseDate("1/2/26");  // Python %Y accepts short years -> year 26
    CHECK(d && d->y == 26, "short year accepted like %Y");
    CHECK(!parseDate("31/02/2026"), "reject Feb 31");
    CHECK(!parseDate("32/01/2026"), "reject day 32");
    CHECK(!parseDate("29/02/2026"), "reject non-leap Feb 29");
    CHECK(parseDate("29/02/2024").has_value(), "accept leap Feb 29");
    CHECK(!parseDate("0/01/2026"), "reject day 0");
    CHECK(!parseDate("22/07/0"), "reject year 0 (datetime MINYEAR=1)");
    CHECK(!parseDate(""), "reject empty");
    CHECK(!parseDate("banana"), "reject garbage");
    CHECK(!parseDate("22/07"), "reject two fields");
    CHECK(!parseDate("22/07/2026/1"), "reject four fields");
    CHECK(!parseDate("22-07-2026"), "reject DD-MM-YYYY (not a Python format)");
    CHECK((Ymd{10000, 0, 0}) < (Ymd{10000, 0, 1}), "Ymd compare sanity");
    Ymd valid{9999, 12, 31};
    CHECK(valid < (Ymd{10000, 0, 0}), "unparseable sentinel sorts after every valid date");
}

// ── sorting & filtering parity ────────────────────────────────────────

static void testSortFilter() {
    NSMutableArray *todos = [NSMutableArray array];
    [todos addObject:mkTodo("beta", "Low", "Work", "", false, "19/07/2026")];      // 0
    [todos addObject:mkTodo("Alpha", "High", "General", "01/01/2026", true, "18/07/2026")]; // 1
    [todos addObject:mkTodo("gamma", "Medium", "Work", "22/07/2026", false, "19/07/2026")]; // 2
    [todos addObject:mkTodo("delta", "High", "Personal", "bad-date", false, "20/07/2026")]; // 3

    auto rows = visibleRows(todos, "all", "All", "All", "", false);
    CHECK(rows.size() == 4 && rows[0].realIndex == 0 && rows[3].realIndex == 3,
          "no sort keeps insertion order");

    rows = visibleRows(todos, "active", "All", "All", "", false);
    CHECK(rows.size() == 3 && rows[0].realIndex == 0, "active filter drops done");

    rows = visibleRows(todos, "completed", "All", "All", "", false);
    CHECK(rows.size() == 1 && rows[0].realIndex == 1, "completed filter keeps done");

    rows = visibleRows(todos, "all", "High", "All", "", false);
    CHECK(rows.size() == 2 && rows[0].realIndex == 1 && rows[1].realIndex == 3,
          "priority filter");

    rows = visibleRows(todos, "all", "All", "Work", "", false);
    CHECK(rows.size() == 2 && rows[0].realIndex == 0 && rows[1].realIndex == 2,
          "category filter");

    rows = visibleRows(todos, "all", "All", "All", "priority", false);
    CHECK(rows[0].realIndex == 1 && rows[1].realIndex == 3 && rows[2].realIndex == 2 &&
              rows[3].realIndex == 0,
          "priority sort High<Medium<Low, stable among equals");

    rows = visibleRows(todos, "all", "All", "All", "priority", true);
    // Python list.sort(reverse=True) keeps original order among equal keys,
    // so the two Highs stay as (1, 3) even in the reversed order
    CHECK(rows[0].realIndex == 0 && rows[1].realIndex == 2 && rows[2].realIndex == 1 &&
              rows[3].realIndex == 3,
          "reverse priority sort, original order preserved among equal Highs");

    rows = visibleRows(todos, "all", "All", "All", "text", false);
    CHECK(rows[0].realIndex == 1 && rows[1].realIndex == 0 && rows[2].realIndex == 3 &&
              rows[3].realIndex == 2,
          "text sort is case-insensitive (Alpha < beta < delta < gamma)");

    rows = visibleRows(todos, "all", "All", "All", "due", false);
    // valid dates ascending (01/01 < 22/07), then no-date and bad-date at the end
    CHECK(rows[0].realIndex == 1 && rows[1].realIndex == 2 && rows[2].realIndex == 0 &&
              rows[3].realIndex == 3,
          "due sort: unparseable dates sort last, stable");

    rows = visibleRows(todos, "all", "All", "All", "done", false);
    CHECK(rows[0].realIndex == 1, "done sort: done rows first");

    rows = visibleRows(todos, "all", "All", "All", "bogus_col", false);
    CHECK(rows.size() == 4 && rows[0].realIndex == 0 && rows[3].realIndex == 3,
          "unknown sort column leaves order unchanged (todo.get(col,'') parity)");
}

// ── geometry & state ──────────────────────────────────────────────────

static void testGeometryState() {
    auto g = parseGeometry("1064x550+1008+70");
    CHECK(g && g->w == 1064 && g->h == 550 && g->x == 1008 && g->y == 70, "parse geometry");
    g = parseGeometry("900x550+-1050+70");  // Tk's negative-offset form
    CHECK(g && g->x == -1050, "parse negative x offset (+-)");
    g = parseGeometry("900x550-100-20");
    CHECK(g && g->x == -100 && g->y == -20, "parse bare-minus offsets");
    CHECK(!parseGeometry(""), "reject empty geometry");
    CHECK(!parseGeometry("axb+1+2"), "reject garbage geometry");
    CHECK(formatGeometry(900, 550, 10, 20) == "900x550+10+20", "format geometry");

    std::string dir = tmpDir("state");
    std::string f = pathJoin(dir, "todo_state.json");

    // The exact schema TodoList.py writes (incl. sort_col null) must load
    writeText(f, "{\n  \"geometry\": \"1064x550+1008+70\",\n  \"filter_done\": \"active\",\n"
                 "  \"filter_priority\": \"High\",\n  \"filter_category\": \"Work\",\n"
                 "  \"sort_col\": null,\n  \"sort_reverse\": false\n}");
    UiState s = loadState(f);
    CHECK(s.geometry == "1064x550+1008+70" && s.filterDone == "active" &&
              s.filterPriority == "High" && s.filterCategory == "Work" && s.sortCol.empty() &&
              !s.sortReverse,
          "load Python-written state");

    s.sortCol = "due";
    s.sortReverse = true;
    CHECK(saveState(f, s), "save state");
    UiState s2 = loadState(f);
    CHECK(s2.sortCol == "due" && s2.sortReverse && s2.filterDone == "active",
          "state round-trip");
    s2.sortCol = "";
    saveState(f, s2);
    CHECK(readText(f).find("null") != std::string::npos, "empty sort_col saved as JSON null");

    UiState d = loadState(pathJoin(dir, "missing.json"));
    CHECK(d.filterDone == "all" && d.filterPriority == "All" && d.geometry.empty(),
          "missing state file yields defaults");
}

// ── loadData / writeDataFile contract ─────────────────────────────────

static void testLoadSave() {
    std::string dir = tmpDir("data");
    std::string f = pathJoin(dir, "todos.json");
    NSMutableArray *todos = [NSMutableArray array];
    NSMutableArray *cats = [NSMutableArray arrayWithObjects:@"General", @"Work", nil];
    Mtime mtime = std::make_pair(1LL, 1L);

    CHECK(loadData(f, todos, cats, &mtime) == LoadResult::Missing && !mtime,
          "missing file: mtime cleared, no crash");

    [todos addObject:mkTodo("keepme", "High", "General", "", false, "19/07/2026")];
    writeText(f, "{ not json !");
    CHECK(loadData(f, todos, cats, &mtime) == LoadResult::Corrupt, "corrupt file detected");
    CHECK(mtime.has_value(), "corrupt file: mtime remembered so the poll stops re-reading");
    CHECK(todos.count == 1, "corrupt file: in-memory todos kept");

    writeText(f, "{\"todos\": [{\"text\": \"from disk\", \"priority\": \"Low\","
                 "\"category\": \"X\", \"due\": \"\", \"done\": true,"
                 "\"created\": \"01/01/2026\"}], \"categories\": []}");
    CHECK(loadData(f, todos, cats, &mtime) == LoadResult::Loaded, "load valid file");
    CHECK(todos.count == 1 && [todoStr(todos[0], @"text") isEqualToString:@"from disk"],
          "todos replaced from disk");
    CHECK(todoDone(todos[0]), "done bool survives");
    CHECK(cats.count == 2 && [cats containsObject:@"Work"],
          "empty saved categories list leaves current categories untouched");

    writeText(f, "{\"todos\": [], \"categories\": [\"A\", \"B\"]}");
    loadData(f, todos, cats, &mtime);
    CHECK(cats.count == 2 && [cats[0] isEqualToString:@"A"], "non-empty categories adopted");

    // write: atomic, records mtime, then reload round-trips
    [todos removeAllObjects];
    [todos addObject:mkTodo("round trip \xE2\x9C\x93 caf\xC3\xA9", "Medium", "General", "22/07/2026",
                            false, "20/07/2026")];
    Mtime wm;
    CHECK(writeDataFile(f, todos, cats, &wm) && wm.has_value(), "writeDataFile");
    CHECK(!fileExists(f + ".tmp"), "tmp file renamed away");
    NSMutableArray *todos2 = [NSMutableArray array];
    NSMutableArray *cats2 = [NSMutableArray array];
    Mtime m2;
    CHECK(loadData(f, todos2, cats2, &m2) == LoadResult::Loaded && todos2.count == 1,
          "reload own write");
    CHECK([todoStr(todos2[0], @"text") isEqualToString:@"round trip ✓ café"],
          "unicode survives the round trip");
    CHECK(m2 == wm, "recorded mtime matches disk");

    // unknown keys written by a future TodoList.py must survive load -> save
    writeText(f, "{\"todos\": [{\"text\": \"x\", \"priority\": \"Low\", \"category\": \"G\","
                 "\"due\": \"\", \"done\": false, \"created\": \"01/01/2026\","
                 "\"snoozed_until\": \"2027-01-01\"}], \"categories\": [\"G\"]}");
    loadData(f, todos2, cats2, &m2);
    writeDataFile(f, todos2, cats2, nullptr);
    CHECK(readText(f).find("snoozed_until") != std::string::npos,
          "unknown todo keys preserved across load->save");

    // parent dir vanishing (OneDrive GC of an empty dir) is recreated on save
    std::string sub = pathJoin(dir, "gone");
    std::string f2 = pathJoin(sub, "todos.json");
    makeDirs(sub);
    rmdir(sub.c_str());
    CHECK(writeDataFile(f2, todos2, cats2, nullptr) && fileExists(f2),
          "save recreates a GC'd parent dir");
}

// ── conflict-fork absorption ──────────────────────────────────────────

static void testAbsorbForks() {
    std::string dir = tmpDir("forks");
    std::string f = pathJoin(dir, "todos.json");
    NSMutableArray *todos = [NSMutableArray array];
    NSMutableArray *cats = [NSMutableArray arrayWithObjects:@"General", nil];
    [todos addObject:mkTodo("shared", "High", "General", "", false, "19/07/2026")];

    CHECK(!absorbForks(f, todos, cats), "no forks -> unchanged");

    // fork: one duplicate of "shared" (winner's version kept), one unique, one new category
    writeText(pathJoin(dir, "todos-MacMini.json"),
              "{\"todos\": ["
              "{\"text\": \"shared\", \"priority\": \"Low\", \"category\": \"General\","
              " \"due\": \"\", \"done\": true, \"created\": \"19/07/2026\"},"
              "{\"text\": \"only on fork\", \"priority\": \"Medium\", \"category\": \"Forked\","
              " \"due\": \"\", \"done\": false, \"created\": \"18/07/2026\"}],"
              "\"categories\": [\"General\", \"Forked\"]}");
    CHECK(absorbForks(f, todos, cats), "fork absorbed -> changed");
    CHECK(todos.count == 2, "unique fork todo appended");
    CHECK(!todoDone(todos[0]), "winner's version kept for the duplicate identity");
    CHECK([cats containsObject:@"Forked"] && cats.count == 2, "categories unioned");
    CHECK(!fileExists(pathJoin(dir, "todos-MacMini.json")), "fork deleted after absorb");

    // identity is (text, created): same text with a different created is a new item
    writeText(pathJoin(dir, "todos-Laptop.json"),
              "{\"todos\": [{\"text\": \"shared\", \"priority\": \"High\","
              " \"category\": \"General\", \"due\": \"\", \"done\": false,"
              " \"created\": \"01/01/2020\"}], \"categories\": []}");
    CHECK(absorbForks(f, todos, cats) && todos.count == 3,
          "same text, different created -> distinct identity, merged");

    // an unreadable fork is left in place for the next poll to retry
    writeText(pathJoin(dir, "todos-Broken.json"), "not json at all");
    CHECK(!absorbForks(f, todos, cats), "unreadable fork absorbs nothing");
    CHECK(fileExists(pathJoin(dir, "todos-Broken.json")), "unreadable fork left for retry");
    unlink(pathJoin(dir, "todos-Broken.json").c_str());

    // absorbing a fork with no new content still deletes it (delete syncs everywhere)
    writeText(pathJoin(dir, "todos-Noop.json"),
              "{\"todos\": [], \"categories\": [\"General\"]}");
    CHECK(!absorbForks(f, todos, cats), "no-new-content fork -> changed == false");
    CHECK(!fileExists(pathJoin(dir, "todos-Noop.json")), "no-new-content fork still deleted");
}

// ── OneDrive discovery / legacy migration / data-file resolution ──────

static void testResolvePaths() {
    // findOneDriveRoot prefers OneDrive-Personal, falls back sorted-first, then ~/OneDrive
    std::string home = tmpDir("home1");
    makeDirs(pathJoin(home, "Library/CloudStorage/OneDrive-Work"));
    makeDirs(pathJoin(home, "Library/CloudStorage/OneDrive-Personal"));
    CHECK(findOneDriveRoot(home) == pathJoin(home, "Library/CloudStorage/OneDrive-Personal"),
          "OneDrive-Personal preferred");

    std::string home2 = tmpDir("home2");
    makeDirs(pathJoin(home2, "Library/CloudStorage/OneDrive-Contoso"));
    CHECK(findOneDriveRoot(home2) == pathJoin(home2, "Library/CloudStorage/OneDrive-Contoso"),
          "single business account used");

    std::string home3 = tmpDir("home3");
    makeDirs(pathJoin(home3, "OneDrive"));
    CHECK(findOneDriveRoot(home3) == pathJoin(home3, "OneDrive"), "legacy ~/OneDrive fallback");

    std::string home4 = tmpDir("home4");
    makeDirs(home4);
    CHECK(findOneDriveRoot(home4).empty(), "no OneDrive -> empty");

    // migrateLegacyDir: free slot moves the file across and removes the old dir
    std::string od = tmpDir("od1");
    std::string shared = pathJoin(od, SHARED_SUBDIR);
    makeDirs(shared);
    writeText(pathJoin(od, "TodoList/todos.json"), "{\"todos\": [], \"categories\": []}");
    migrateLegacyDir(od, shared);
    CHECK(fileExists(pathJoin(shared, "todos.json")), "legacy file moved into shared dir");
    CHECK(!isDir(pathJoin(od, "TodoList")), "empty legacy dir removed");

    // occupied slot: conflict-fork names TodoListLegacy, then Legacy2, Legacy3
    writeText(pathJoin(od, "TodoList/todos.json"), "{\"a\": 1}");
    migrateLegacyDir(od, shared);
    CHECK(fileExists(pathJoin(shared, "todos-TodoListLegacy.json")),
          "occupied slot -> TodoListLegacy fork name");
    writeText(pathJoin(od, "TodoList/todos.json"), "{\"b\": 2}");
    migrateLegacyDir(od, shared);
    CHECK(fileExists(pathJoin(shared, "todos-TodoListLegacy2.json")),
          "second migration -> Legacy2 (Python's '' -> 2 sequence)");
    writeText(pathJoin(od, "TodoList/todos.json"), "{\"c\": 3}");
    migrateLegacyDir(od, shared);
    CHECK(fileExists(pathJoin(shared, "todos-TodoListLegacy3.json")),
          "third migration -> Legacy3");

    // a non-empty legacy dir survives (rmdir best-effort)
    std::string od2 = tmpDir("od2");
    makeDirs(pathJoin(od2, SHARED_SUBDIR));
    writeText(pathJoin(od2, "TodoList/todos.json"), "{}");
    writeText(pathJoin(od2, "TodoList/other.txt"), "x");
    migrateLegacyDir(od2, pathJoin(od2, SHARED_SUBDIR));
    CHECK(fileExists(pathJoin(od2, pathJoin(SHARED_SUBDIR, "todos.json"))),
          "file migrated from non-empty legacy dir");
    CHECK(isDir(pathJoin(od2, "TodoList")), "non-empty legacy dir left in place");

    // resolveDataFile with an explicit override: uses it and seeds from local
    std::string base = tmpDir("base1");
    writeText(pathJoin(base, "todos.json"), "{\"todos\": [{\"text\": \"local seed\","
              "\"priority\": \"Low\", \"category\": \"G\", \"due\": \"\", \"done\": false,"
              "\"created\": \"01/01/2026\"}], \"categories\": [\"G\"]}");
    std::string ovr = pathJoin(gTmpRoot, "override1");
    std::string got = resolveDataFile(base, tmpDir("homeX"), ovr.c_str());
    CHECK(got == pathJoin(ovr, "todos.json"), "override dir honoured");
    CHECK(readText(got).find("local seed") != std::string::npos,
          "empty shared slot seeded from local file");

    // a second machine joining later adopts the synced file, never overwrites it
    std::string base2 = tmpDir("base2");
    writeText(pathJoin(base2, "todos.json"), "{\"todos\": [], \"categories\": [\"MACHINE2\"]}");
    std::string got2 = resolveDataFile(base2, tmpDir("homeX2"), ovr.c_str());
    CHECK(got2 == got, "second machine resolves the same shared file");
    CHECK(readText(got2).find("local seed") != std::string::npos &&
              readText(got2).find("MACHINE2") == std::string::npos,
          "existing shared file adopted, not overwritten");

    // no OneDrive, no override -> local fallback
    std::string got3 = resolveDataFile(base, tmpDir("home5"), nullptr);
    CHECK(got3 == pathJoin(base, "todos.json"), "no OneDrive -> local file");

    // full OneDrive path: fake home, auto-migration of the legacy dir included
    std::string home6 = tmpDir("home6");
    std::string odp = pathJoin(home6, "Library/CloudStorage/OneDrive-Personal");
    writeText(pathJoin(odp, "TodoList/todos.json"),
              "{\"todos\": [], \"categories\": [\"FROM_LEGACY\"]}");
    std::string base3 = tmpDir("base3");
    std::string got4 = resolveDataFile(base3, home6, nullptr);
    CHECK(got4 == pathJoin(odp, "MyAppShare/todos.json"), "OneDrive shared path resolved");
    CHECK(readText(got4).find("FROM_LEGACY") != std::string::npos,
          "legacy dir folded in during resolution");
}

// ── created-date formatting ───────────────────────────────────────────

static void testCreated() {
    std::string c = nowCreatedString();
    CHECK(c.size() == 10 && c[2] == '/' && c[5] == '/', "created is DD/MM/YYYY");
    CHECK(parseDate(c).has_value(), "created parses back");
    Ymd t = todayYmd();
    CHECK(t == *parseDate(c), "created == today");
}

int main(int argc, char *argv[]) {
    @autoreleasepool {
        if (argc == 4 && strcmp(argv[1], "roundtrip") == 0) {
            // Build-script helper: read a (Python-written) todos.json, write it
            // back through the C++ writer for Python to re-verify.
            NSMutableArray *todos = [NSMutableArray array];
            NSMutableArray *cats = [NSMutableArray array];
            Mtime m;
            if (loadData(argv[2], todos, cats, &m) != LoadResult::Loaded) return 2;
            return writeDataFile(argv[3], todos, cats, nullptr) ? 0 : 3;
        }

        char tmpl[] = "/tmp/todolist_native_tests.XXXXXX";
        gTmpRoot = mkdtemp(tmpl);

        testParseDate();
        testSortFilter();
        testGeometryState();
        testLoadSave();
        testAbsorbForks();
        testResolvePaths();
        testCreated();

        printf("%d checks, %d failures\n", gChecks, gFailures);
        [[NSFileManager defaultManager] removeItemAtPath:nstr(gTmpRoot) error:nil];
        return gFailures ? 1 : 0;
    }
}
