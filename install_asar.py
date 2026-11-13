#!/usr/bin/env python3
"""Claude Code 桌面版 (Microsoft Store) 简体中文汉化 —— asar 布局适配版

本脚本是 pvq125800/claude-code-zh-cn (MIT License) 的 fork 增强版。

适配 2026-11 起商店版 (5.11492.x+) 的新布局: 界面资源打包为 ion-dist.asar
单文件 (原 install.py 依赖 ion-dist/ 解包目录, 在新布局上会安全退出),
且 claude.exe 内嵌 asar 完整性哈希 (SHA256 of header JSON), 需同步替换。

原理:
  1. 解析 ion-dist.asar, 以官方 en-US.json 为底稿 + 汉化包叠加, 生成
     i18n/zh-CN.json、i18n/zh-Hans.json(同内容副本) 及 dynamic/ 两个副本
  2. 对 assets/v1/*.js 打三处字符串补丁: 语言白名单加 zh-CN/zh-Hans、
     语言名称表、localeChoice 钉住 zh-CN (每文件过 node --check, 不过即回滚)
  3. 重打包 asar (未改动文件字节原样流式拷贝, 新增/修改文件重算 integrity)
  4. 把 claude.exe 内嵌的 ion-dist.asar 头哈希替换为新哈希 (等长 64 hex)
  5. Claude-3p/config.json locale/language = zh-Hans (主进程内置 zh-Hans shell 翻译)

用法:
  python claude_zh_adapted.py --check     只诊断
  python claude_zh_adapted.py --build     构建新 asar 到 staging, 不动系统
  python claude_zh_adapted.py --install   构建并安装 (需管理员, 会关闭 Claude)
  python claude_zh_adapted.py --uninstall 还原
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BLOCK_SIZE = 4194304

# ---------------- 路径 ----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
                        "claude-code-zh-cn-asar")
BACKUP_DIR = os.path.join(WORK_DIR, "backup")
STAGE_DIR = os.path.join(WORK_DIR, "stage")
MANIFEST = os.path.join(WORK_DIR, "manifest.json")
PACK_DIR_DEFAULT = os.path.join(SCRIPT_DIR, "pack")

CONFIG = os.path.expandvars(r"%LOCALAPPDATA%\Claude-3p\config.json")

RELEASE_REPO = "pvq125800/claude-code-zh-cn"
PACK_FILES = ("frontend-pack.json", "frontend-extra.json",
              "shell-pack.json", "shell-extra.json",
              "dynamic-extra.json", "statsig-pack.json")

# ---------------- JS 补丁目标 ----------------
LANG_LIST_OLD = ('"en-US","de-DE","fr-FR","ko-KR","ja-JP","es-419",'
                 '"es-ES","it-IT","hi-IN","pt-BR","id-ID"')
LANG_LIST_NEW = LANG_LIST_OLD + ',"zh-CN","zh-Hans"'
LOC_NAMES_OLD = '"id-ID":{name:"Indonesian (Indonesia)",localName:"Indonesia (Indonesia)"}'
LOC_NAMES_NEW = (LOC_NAMES_OLD
                 + ',"zh-CN":{name:"Chinese (China)",localName:"\\u7b80\\u4f53\\u4e2d\\u6587 (\\u4e2d\\u56fd)"}'
                 + ',"zh-Hans":{name:"Chinese (Simplified)",localName:"\\u7b80\\u4f53\\u4e2d\\u6587"}')

EXE_HASH_RE = (rb'\{"file":"resources\\\\ion-dist\.asar",'
               rb'"alg":"SHA256","value":"([0-9a-f]{64})"\}')


def log(msg):
    print(msg, flush=True)


def run_ps(script, timeout=90):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip()


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------- asar 读写 ----------------
def read_asar_header(path):
    """返回 (tree, data_start, header_pickle_len)。"""
    with open(path, "rb") as f:
        head = f.read(16)
        _, header_size, _payload, json_len = struct.unpack_from("<IIII", head, 0)
        raw = f.read(json_len)
    return json.loads(raw.decode("utf-8")), 8 + header_size, header_size


def iter_leaves(node, prefix=""):
    """按文档顺序产出 (相对路径, entry)。目录节点原位携带, 便于注入。"""
    if "files" in node:
        for name, child in node["files"].items():
            p = f"{prefix}/{name}" if prefix else name
            yield from iter_leaves(child, p)
    else:
        yield prefix, node


def sha256_hex(blob):
    return hashlib.sha256(blob).hexdigest()


def file_integrity(blob):
    blocks = [sha256_hex(blob[i:i + BLOCK_SIZE])
              for i in range(0, len(blob), BLOCK_SIZE)]
    return {"algorithm": "SHA256", "hash": sha256_hex(blob),
            "blockSize": BLOCK_SIZE, "blocks": blocks}


# ---------------- 定位安装 ----------------
def find_package(cli_dir=None):
    if cli_dir:
        return cli_dir
    loc = run_ps("$p = Get-AppxPackage -Name '*Claude*' | Sort-Object Version -Descending | "
                 "Select-Object -First 1; if ($p) { $p.InstallLocation }")
    if loc and os.path.isdir(loc):
        return loc
    cands = glob.glob(r"C:\Program Files\WindowsApps\Claude_*_x64__pzs8sxrjxfjjc")
    if cands:
        cands.sort(key=os.path.getmtime, reverse=True)
        return cands[0]
    return None


def paths_for(pkg):
    res = os.path.join(pkg, "app", "resources")
    return {"res": res,
            "ion": os.path.join(res, "ion-dist.asar"),
            "exe": os.path.join(pkg, "app", "claude.exe")}


# ---------------- 翻译包 ----------------
def pack_dir(cli_pack=None):
    for d in (cli_pack, PACK_DIR_DEFAULT, os.path.join(SCRIPT_DIR, "resources")):
        if d and all(os.path.isfile(os.path.join(d, n)) for n in PACK_FILES):
            return d
    # 从 Release 下载
    dl = os.path.join(WORK_DIR, "pack")
    if all(os.path.isfile(os.path.join(dl, n)) for n in PACK_FILES):
        return dl
    log("下载翻译包 (GitHub Release)...")
    import urllib.request
    api = f"https://api.github.com/repos/{RELEASE_REPO}/releases/latest"
    req = urllib.request.Request(api, headers={"User-Agent": "claude-code-zh-cn-adapted"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        info = json.load(resp)
    asset = next(a for a in info["assets"] if a["name"].endswith(".zip"))
    zpath = os.path.join(WORK_DIR, "resources-zh-CN.zip")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=300) as resp, \
            open(zpath, "wb") as f:
        f.write(resp.read())
    os.makedirs(dl, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            if os.path.basename(name) in PACK_FILES:
                with zf.open(name) as src, \
                        open(os.path.join(dl, os.path.basename(name)), "wb") as dst:
                    shutil.copyfileobj(src, dst)
    return dl


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def merge(base, *layers):
    out = dict(base)
    for layer in layers:
        for k, v in (layer or {}).items():
            if isinstance(v, str) and v.strip():
                out[k] = v
    return out


def build_translations(pdir, ion_path, out_stage):
    """从 asar 里读 en-US 底稿, 叠加汉化包, 产出 4 个 zh json。"""
    tree, data_start, _ = read_asar_header(ion_path)
    i18n = tree["files"]["i18n"]["files"]
    with open(ion_path, "rb") as f:
        f.seek(data_start + int(i18n["en-US.json"]["offset"]))
        fe_en = json.loads(f.read(i18n["en-US.json"]["size"]).decode("utf-8"))
        f.seek(data_start + int(i18n["dynamic"]["files"]["en-US.json"]["offset"]))
        dyn_en = json.loads(f.read(i18n["dynamic"]["files"]["en-US.json"]["size"]).decode("utf-8"))
    if not fe_en:
        log("[错误] 读不到官方底稿 en-US.json")
        sys.exit(1)

    def res(name):
        return read_json(os.path.join(pdir, name), {})

    fe = merge(fe_en, res("frontend-pack.json"), res("frontend-extra.json"))
    dyn = merge(dyn_en, res("dynamic-extra.json"))
    translated = sum(1 for k, v in fe_en.items() if fe.get(k, v) != v)
    log(f"  前端界面: {translated}/{len(fe_en)} 已译 ({translated * 100 // max(1, len(fe_en))}%)")
    log(f"  动态文案: {sum(1 for k, v in dyn_en.items() if dyn.get(k, v) != v)}/{len(dyn_en)}")

    os.makedirs(out_stage, exist_ok=True)
    for name, data in (("zh-CN.json", fe), ("zh-Hans.json", fe),
                       ("dynamic/zh-CN.json", dyn), ("dynamic/zh-Hans.json", dyn)):
        dest = os.path.join(out_stage, name.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return out_stage


# ---------------- JS 补丁 ----------------
def node_check(text, tmpdir):
    if not shutil.which("node"):
        return None
    tmp = os.path.join(tmpdir, "_zh_syntax_check.mjs")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        return subprocess.run(["node", "--check", tmp],
                              capture_output=True, timeout=120).returncode == 0
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def patch_renderer_js(ion_path, stage_dir):
    """扫描 assets/v1/*.js, 打三处补丁, 补丁后的文件写到 stage_dir 返回 {rel: path}。"""
    tree, data_start, _ = read_asar_header(ion_path)
    v1 = tree["files"]["assets"]["files"]["v1"]["files"]
    tmpdir = tempfile.mkdtemp(prefix="zhcheck_")
    modified = {}
    changed = hits = rolled = 0
    try:
        with open(ion_path, "rb") as f:
            for name, entry in v1.items():
                if not name.endswith(".js"):
                    continue
                f.seek(data_start + int(entry["offset"]))
                orig = f.read(entry["size"]).decode("utf-8", "strict")
                text, n = orig, 0
                if "localeChoice:null" in text:
                    n += text.count("localeChoice:null")
                    text = text.replace("localeChoice:null", 'localeChoice:"zh-CN"')
                if LANG_LIST_OLD in text and LANG_LIST_NEW not in text:
                    n += text.count(LANG_LIST_OLD)
                    text = text.replace(LANG_LIST_OLD, LANG_LIST_NEW)
                if LOC_NAMES_OLD in text and LOC_NAMES_NEW not in text:
                    n += 1
                    text = text.replace(LOC_NAMES_OLD, LOC_NAMES_NEW)
                if text == orig:
                    continue
                if node_check(orig, tmpdir) is True and node_check(text, tmpdir) is False:
                    log(f"  [回滚] 补丁后语法校验不通过: {name}")
                    rolled += 1
                    continue
                dest = os.path.join(stage_dir, "js", name)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8", newline="") as f2:
                    f2.write(text)
                modified[f"assets/v1/{name}"] = dest
                changed += 1
                hits += n
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    log(f"  语言补丁: 改动 {changed} 个 JS / {hits} 处 / 回滚 {rolled}")
    return modified


# ---------------- 重打包 ----------------
def build_asar(src_ion, inject, modified, out_path):
    """inject/modified: rel -> 本地文件路径。返回新 header JSON 的 sha256。"""
    tree, data_start, _ = read_asar_header(src_ion)

    # 注入新文件到树 (追加到对应目录的 files 末尾)
    def add(rel, tag):
        parts = rel.split("/")
        node = tree["files"]
        for p in parts[:-1]:
            node = node[p]["files"]
        node[parts[-1]] = {"__new__": tag}
    for rel in inject:
        add(rel, inject[rel])

    replace = dict(modified)
    replace.update(inject)

    # 第一遍: 流式写数据区, 计算偏移与 integrity
    data_tmp = out_path + ".data"
    pos = 0
    with open(src_ion, "rb") as fsrc, open(data_tmp, "wb") as fdst:
        for rel, entry in iter_leaves(tree):
            if "__new__" in entry:
                with open(entry["__new__"], "rb") as nf:
                    blob = nf.read()
                entry.clear()
                entry["integrity"] = file_integrity(blob)
            elif rel in replace:
                with open(replace[rel], "rb") as nf:
                    blob = nf.read()
                entry.pop("unpacked", None)
                entry["integrity"] = file_integrity(blob)
            else:
                fsrc.seek(data_start + int(entry["offset"]))
                blob = fsrc.read(entry["size"])
            entry["size"] = len(blob)
            entry["offset"] = str(pos)
            fdst.write(blob)
            pos += len(blob)

    # 第二遍: 序列化 header, 组装最终文件
    json_bytes = json.dumps(tree, ensure_ascii=False,
                            separators=(",", ":")).encode("utf-8")
    json_len = len(json_bytes)
    payload_size = (4 + json_len + 3) & ~3
    pickle_len = 4 + payload_size
    data_start_new = 8 + pickle_len
    header = (struct.pack("<IIII", 4, pickle_len, payload_size, json_len)
              + json_bytes + b"\0" * (data_start_new - 16 - json_len))
    header_sha = sha256_hex(json_bytes)

    with open(out_path, "wb") as fout, open(data_tmp, "rb") as fdata:
        fout.write(header)
        shutil.copyfileobj(fdata, fout, 1024 * 1024)
    os.remove(data_tmp)
    return header_sha


# ---------------- exe 哈希 ----------------
def exe_hash_loc(exe):
    data = open(exe, "rb").read()
    m = re.search(EXE_HASH_RE, data)
    if not m:
        return None, None, None
    return m.start(1), m.group(1).decode(), data


def verify_hash_formula(ion_path, exe):
    """校验: exe 内嵌哈希 == sha256(当前 asar header JSON)。失败说明布局变了。"""
    off, old_hash, _ = exe_hash_loc(exe)
    if off is None:
        return None
    with open(ion_path, "rb") as f:
        head = f.read(16)
        _, _, _, json_len = struct.unpack_from("<IIII", head, 0)
        raw = f.read(json_len)
    actual = sha256_hex(raw)
    return {"match": actual == old_hash, "embedded": old_hash, "actual": actual,
            "offset": off}


def patch_exe_hash(exe, old_hash, new_hash, offset):
    if old_hash == new_hash:
        return
    with open(exe, "r+b") as f:
        f.seek(offset)
        cur = f.read(64).decode()
        if cur != old_hash:
            raise RuntimeError(f"exe 哈希与预期不符: {cur[:16]}... != {old_hash[:16]}...")
        f.seek(offset)
        f.write(new_hash.encode())


# ---------------- 进程 / ACL ----------------
def close_claude():
    n = run_ps("Get-Process claude,claude-3p,cowork-svc -ErrorAction SilentlyContinue | "
               "Stop-Process -Force; Start-Sleep -Milliseconds 800; "
               "(Get-Process claude,claude-3p,cowork-svc -ErrorAction SilentlyContinue | "
               "Measure-Object).Count", timeout=120)
    log(f"  已关闭 Claude, 剩余进程: {n}")


def grant_acl(path):
    is_file = os.path.isfile(path)
    inherit = "" if is_file else "(OI)(CI)"
    recurse = [] if is_file else ["/R", "/D", "Y"]
    sid = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    sid = sid.strip('"').split('","')[-1]
    log("  takeown ...")
    subprocess.run(["takeown", "/F", path, "/A"] + recurse,
                   capture_output=True, timeout=1800)
    log("  icacls 授权 ...")
    subprocess.run(["icacls", path, "/grant", f"*S-1-5-32-544:{inherit}(F)", "/Q"],
                   capture_output=True, timeout=1800)
    if sid:
        subprocess.run(["icacls", path, "/grant", f"*{sid}:{inherit}(F)", "/Q"],
                       capture_output=True, timeout=1800)


def restore_owner(path):
    log("  所有权交还 TrustedInstaller ...")
    subprocess.run(f'icacls "{path}" /setowner "NT SERVICE\\TrustedInstaller" /T /Q >nul 2>&1',
                   shell=True, timeout=1800)


# ---------------- install / uninstall / check ----------------
def do_check(pkg):
    p = paths_for(pkg)
    log(f"包: {pkg}")
    log(f"asar: {p['ion']}  存在={os.path.isfile(p['ion'])}")
    log(f"exe : {p['exe']}  存在={os.path.isfile(p['exe'])}")
    if not (os.path.isfile(p["ion"]) and os.path.isfile(p["exe"])):
        return 1
    try:
        tree, _, _ = read_asar_header(p["ion"])
        i18n = tree["files"].get("i18n", {}).get("files", {})
        log(f"asar 顶层: {', '.join(list(tree['files'].keys()))}")
        log(f"i18n locales: {sorted(k for k in i18n if k.endswith('.json'))}")
        has_zh = "zh-CN.json" in i18n
        log(f"已存在 zh-CN.json: {has_zh}")
    except Exception as e:
        log(f"[错误] asar 解析失败: {e}")
        return 1
    info = verify_hash_formula(p["ion"], p["exe"])
    if info is None:
        log("[错误] exe 中找不到 ion-dist.asar 的完整性哈希")
        return 1
    log(f"哈希公式校验: {'一致 ✓' if info['match'] else '不一致 ✗ (版本布局可能已变化)'}")
    log(f"  exe 内嵌: {info['embedded'][:32]}...")
    log(f"  实际计算: {info['actual'][:32]}...")
    return 0 if info["match"] else 1


def do_install(pkg, cli_pack=None, build_only=False):
    if not build_only and not is_admin():
        log("[错误] 安装需要管理员权限")
        return 1
    p = paths_for(pkg)
    info = verify_hash_formula(p["ion"], p["exe"])
    if not info or not info["match"]:
        log("[错误] exe 内嵌哈希与 asar 实际不匹配, 拒绝安装 (版本布局可能已变化)")
        return 1

    log("[1/5] 构建翻译...")
    os.makedirs(STAGE_DIR, exist_ok=True)
    pdir = pack_dir(cli_pack)
    zh_stage = build_translations(pdir, p["ion"], os.path.join(STAGE_DIR, "i18n"))

    log("[2/5] JS 语言补丁...")
    inject = {
        "i18n/zh-CN.json": os.path.join(zh_stage, "zh-CN.json"),
        "i18n/zh-Hans.json": os.path.join(zh_stage, "zh-Hans.json"),
        "i18n/dynamic/zh-CN.json": os.path.join(zh_stage, "dynamic", "zh-CN.json"),
        "i18n/dynamic/zh-Hans.json": os.path.join(zh_stage, "dynamic", "zh-Hans.json"),
    }
    modified = patch_renderer_js(p["ion"], STAGE_DIR)
    if not modified:
        log("  [警告] 没有文件被补丁 —— 检查补丁目标是否存在")

    log("[3/5] 重打包 ion-dist.asar ...")
    out_asar = os.path.join(STAGE_DIR, "ion-dist.asar.new")
    new_sha = build_asar(p["ion"], inject, modified, out_asar)
    log(f"  新 header sha256: {new_sha}")
    log(f"  新文件大小: {os.path.getsize(out_asar) // 1024 // 1024} MB")

    # 自校验: 解析新 asar 并抽查
    t2, ds2, _ = read_asar_header(out_asar)
    zh_entry = t2["files"]["i18n"]["files"]["zh-CN.json"]
    with open(out_asar, "rb") as f:
        f.seek(ds2 + int(zh_entry["offset"]))
        sample = f.read(zh_entry["size"])
    if sha256_hex(sample) != zh_entry["integrity"]["hash"]:
        log("[错误] 自校验失败: zh-CN.json 完整性不符")
        return 1
    log("  自校验通过 (新 asar 可解析, zh-CN.json 完整性一致)")

    if build_only:
        log("\n--build 完成, 未改动任何系统文件。staging: " + STAGE_DIR)
        return 0

    log("[4/5] 部署 (备份 → 关闭 Claude → 替换 → exe 哈希)...")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    close_claude()
    grant_acl(p["res"])
    grant_acl(p["exe"])  # claude.exe 在 app/ 目录, 需单独授权 (哈希补丁要写它)

    bak_ion = os.path.join(BACKUP_DIR, "ion-dist.asar")
    if not os.path.exists(bak_ion):
        log("  备份原 asar (207MB)...")
        shutil.copy2(p["ion"], bak_ion)
    bak_cfg = os.path.join(BACKUP_DIR, "config.json")
    if os.path.exists(CONFIG) and not os.path.exists(bak_cfg):
        shutil.copy2(CONFIG, bak_cfg)

    shutil.copy2(out_asar, p["ion"])
    patch_exe_hash(p["exe"], info["embedded"], new_sha, info["offset"])

    log("[5/5] 更新 config.json ...")
    if os.path.exists(CONFIG):
        cfg = read_json(CONFIG, {})
        cfg["locale"] = "zh-Hans"
        cfg["language"] = "zh-Hans"
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1, ensure_ascii=False)
        log("  locale/language -> zh-Hans (主进程用内置简中 shell 翻译)")

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump({"package": pkg, "new_header_sha256": new_sha,
                   "exe_old_hash": info["embedded"], "exe_offset": info["offset"],
                   "asar_backup": bak_ion, "config_backup": bak_cfg}, f, indent=1)

    log("\n汉化完成! 启动 Claude 即可看到中文界面。")
    log(f"备份在 {BACKUP_DIR}, 出问题可用 --uninstall 一键还原。")
    return 0


def do_uninstall(pkg):
    if not is_admin():
        log("[错误] 还原需要管理员权限")
        return 1
    p = paths_for(pkg)
    m = read_json(MANIFEST, {})
    if not m:
        log("[错误] 没有找到安装记录 (manifest.json)")
        return 1
    close_claude()
    grant_acl(p["res"])
    grant_acl(p["exe"])  # 还原 exe 内嵌哈希需要写权限
    if m.get("asar_backup") and os.path.isfile(m["asar_backup"]):
        shutil.copy2(m["asar_backup"], p["ion"])
        log("  ion-dist.asar 已还原")
    if m.get("exe_offset") is not None and m.get("exe_old_hash"):
        patch_exe_hash(p["exe"], sha256_of_current(p["exe"], m["exe_offset"]),
                       m["exe_old_hash"], m["exe_offset"])
        log("  claude.exe 内嵌哈希已还原")
    if m.get("config_backup") and os.path.isfile(m["config_backup"]) and os.path.exists(CONFIG):
        shutil.copy2(m["config_backup"], CONFIG)
        log("  config.json 已还原")
    restore_owner(p["res"])
    if os.path.exists(MANIFEST):
        os.remove(MANIFEST)
    log("已还原英文原版, 启动 Claude 生效。")
    return 0


def sha256_of_current(exe, offset):
    with open(exe, "r+b") as f:
        f.seek(offset)
        return f.read(64).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dir", help="手动指定安装目录")
    ap.add_argument("--pack", help="翻译包目录 (含 6 个 json)")
    args = ap.parse_args()
    if not any((args.check, args.build, args.install, args.uninstall)):
        ap.print_help()
        return 1
    os.makedirs(WORK_DIR, exist_ok=True)
    pkg = find_package(args.dir)
    if not pkg:
        log("[错误] 未找到 Claude (Microsoft Store 版)")
        return 1
    if args.check:
        return do_check(pkg)
    if args.uninstall:
        return do_uninstall(pkg)
    return do_install(pkg, cli_pack=args.pack, build_only=args.build)


if __name__ == "__main__":
    sys.exit(main())
