/**
 * «Ни одной строки без перевода» (plan step 10). Fails (exit 1) when:
 *   1. the key trees of en and ru differ — plural suffixes are folded first, so English
 *      `_one/_other` and Russian `_one/_few/_many/_other` count as one key;
 *   2. a key's {{placeholders}} differ between en and ru;
 *   3. a value is empty;
 *   4. the code asks for a key that does not exist: every literal t("a.b") / i18nKey="a.b" in
 *      src (a key with plural forms counts as present); template keys t(`nav.${id}`) are skipped;
 *   5. Cyrillic shows up in code outside comments — Russian text belongs in the locales. A line
 *      marked `// i18n-ok` is allowed (data matching, e.g. a regex for Cyrillic).
 *
 * Run: npm run i18n:check
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = "src/locales";
const LANGS = ["en", "ru"];
const PLURAL = /_(zero|one|two|few|many|other)$/;

const flatten = (obj, prefix = "", out = new Map()) => {
    for (const [k, v] of Object.entries(obj)) {
        const key = prefix ? `${prefix}.${k}` : k;
        if (v && typeof v === "object") flatten(v, key, out);
        else out.set(key, String(v));
    }
    return out;
};

const errors = [];
const fail = (msg) => errors.push(msg);

// --- 1–3: locale files --------------------------------------------------------------------------

const namespaces = readdirSync(join(ROOT, "en")).filter((f) => f.endsWith(".json"));
const known = new Set(); // folded keys of en, for the code check
for (const ns of namespaces) {
    const flat = Object.fromEntries(LANGS.map((l) => [l, flatten(JSON.parse(readFileSync(join(ROOT, l, ns), "utf8")))]));
    const folded = {};
    for (const l of LANGS) {
        folded[l] = new Map();
        for (const [k, v] of flat[l]) {
            if (!v.trim()) fail(`${l}/${ns}: empty value ${k}`);
            const base = k.replace(PLURAL, "");
            const vars = new Set([...v.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]));
            const prev = folded[l].get(base);
            folded[l].set(base, prev ? new Set([...prev, ...vars]) : vars);
        }
    }
    for (const k of folded.en.keys()) known.add(k);
    for (const [a, b] of [["en", "ru"], ["ru", "en"]]) {
        const missing = [...folded[a].keys()].filter((k) => !folded[b].has(k));
        if (missing.length) fail(`${b}/${ns}: missing (present in ${a})\n  ${missing.join("\n  ")}`);
    }
    for (const [k, vars] of folded.en) {
        const ru = folded.ru.get(k);
        if (!ru) continue;
        // {{count}} may be dropped in a plural form («Одна новая») — compare without it.
        const a = [...vars].filter((v) => v !== "count").sort().join(",");
        const b = [...ru].filter((v) => v !== "count").sort().join(",");
        if (a !== b) fail(`${ns}: placeholders differ in ${k}: en {${a}} ≠ ru {${b}}`);
    }
}

// --- 4–5: the code ------------------------------------------------------------------------------

const files = [];
const walk = (dir) => {
    for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
            // src/pages/dev: internal showcases (/dev/ui, /dev/live) compared with the Russian boards
            if (name !== "locales" && name !== "node_modules" && !p.replaceAll("\\", "/").endsWith("pages/dev")) walk(p);
        } else if (/\.(tsx?|mjs)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
            files.push(p);
        }
    }
};
walk("src");

/** Blank out comments (keeping line breaks and string contents). */
function stripComments(src) {
    let out = "";
    let i = 0;
    let quote = null;
    while (i < src.length) {
        const c = src[i];
        const n = src[i + 1];
        if (quote) {
            out += c;
            if (c === "\\") { out += n ?? ""; i += 2; continue; }
            if (c === quote) quote = null;
            i++;
            continue;
        }
        if (c === '"' || c === "'" || c === "`") { quote = c; out += c; i++; continue; }
        if (c === "/" && n === "/") {
            while (i < src.length && src[i] !== "\n") i++;
            continue;
        }
        if (c === "/" && n === "*") {
            const end = src.indexOf("*/", i + 2);
            const chunk = src.slice(i, end < 0 ? src.length : end + 2);
            out += chunk.replace(/[^\n]/g, " ");
            i += chunk.length;
            continue;
        }
        out += c;
        i++;
    }
    return out;
}

const KEY_CALL = /\bt\(\s*["']([\w.-]+)["']/g;
const KEY_ATTR = /i18nKey=["']([\w.-]+)["']/g;
const CYRILLIC = /[Ѐ-ӿ]/;

for (const file of files) {
    const raw = readFileSync(file, "utf8");
    const rel = relative(".", file).replaceAll("\\", "/");
    const code = stripComments(raw);
    for (const re of [KEY_CALL, KEY_ATTR]) {
        for (const m of code.matchAll(re)) {
            const key = m[1];
            if (!key.includes(".")) continue; // not a namespaced key (a plain word passed to some t())
            if (!known.has(key) && !known.has(key.replace(PLURAL, ""))) {
                const line = code.slice(0, m.index).split("\n").length;
                fail(`${rel}:${line}: unknown key "${key}"`);
            }
        }
    }
    const rawLines = raw.split("\n");
    code.split("\n").forEach((line, i) => {
        if (CYRILLIC.test(line) && !rawLines[i].includes("i18n-ok")) {
            fail(`${rel}:${i + 1}: Russian text in code — move it to the locales: ${line.trim().slice(0, 80)}`);
        }
    });
}

if (errors.length) {
    for (const e of errors) console.error(`ERROR ${e}`);
    console.error(`\n${errors.length} problem(s)`);
    process.exit(1);
}
console.log(`OK    ${namespaces.length} namespace(s), ${known.size} keys, ${files.length} files`);
