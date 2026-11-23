"""웹 대시보드(equity_research_dashboard)의 기업데이터 > 내부자 거래 화면으로 내보내기.

    python site_export.py <저장소>/dashboard/insider

viewer/ 화면과 데이터를 복사하되, 기업 파일(cdata)에서 종가 배열은 뺀다.
웹에서는 증시데이터가 이미 올려 두는 종목차트 파일(markets/charts/<코드>.js)로 차트를 그리므로
같은 종가를 두 번 올릴 필요가 없다(기업 파일 17MB → 6MB 안팎).
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VIEWER = ROOT / "viewer"
PAGES = ["trades.html", "gifts.html", "bulk.html", "company.html", "common.js", "common.css"]


def strip_prices(src: Path, dst: Path) -> None:
    s = src.read_text(encoding="utf-8")
    head, body = s.split("=", 1)
    d = json.loads(body.rstrip().rstrip(";"))
    p = d.pop("prices", None) or {}
    if p.get("mcap") and p.get("c"):
        d["shares"] = round(p["mcap"] / p["c"][-1])   # 시총은 웹 차트의 최신 종가 × 주식수로 다시 계산
    dst.write_text(head + "=" + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")


def export(out: Path) -> None:
    (out / "data").mkdir(parents=True, exist_ok=True)
    cdir = out / "cdata"
    if cdir.exists():
        shutil.rmtree(cdir)
    cdir.mkdir()
    for f in PAGES:
        shutil.copy2(VIEWER / f, out / f)
    for f in ("main.js", "bulk.js"):
        shutil.copy2(VIEWER / "data" / f, out / "data" / f)
    n, idx = 0, []
    for f in sorted((VIEWER / "cdata").glob("*.js")):
        if f.name == "index.js":
            continue
        strip_prices(f, cdir / f.name)
        n += 1
        s = f.read_text(encoding="utf-8")
        d = json.loads(s.split("=", 1)[1].rstrip().rstrip(";"))
        idx.append([d["code"], (d.get("corp") or [d["code"]])[0]])
    (cdir / "index.js").write_text("window.CIDX=" + json.dumps(idx, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"내보냄: {out} · 기업 파일 {n}개 · 합계 {size / 1e6:.1f}MB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    export(Path(sys.argv[1]))
