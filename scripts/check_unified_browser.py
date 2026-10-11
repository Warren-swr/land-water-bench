"""Validate task attribution and the combined page against its saved data, without API calls."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8736/")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chromium")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.wait_for_selector('#prediction[aria-busy="false"]')
        data = page.request.get(args.url + "data.json").json()
        ref = page.request.get(args.url + "reference.details.json").json()
        assert page.locator("#ranking tr").count() == len(data["models"])
        assert "gpt-6-1-sol" not in page.locator("#ranking").inner_text()
        assert len(ref["points"]) == 16200
        pct = lambda n: f"{100*n:.2f}%"
        scores = []
        for model in data["models"]:
            cells = page.locator('#ranking tr[data-model="' + model["name"] + '"] td')
            if model["terrain"]:
                score = model["terrain"]["metrics"]["joint_accuracy"]["500"]
                scores.append(score)
                assert cells.nth(1).inner_text().replace("\n", "") == pct(score)
            else:
                assert cells.nth(1).inner_text() == "—"
            expected = pct(model["classification"]["metrics"]["weighted_accuracy"]) if model["classification"] else "—"
            assert cells.nth(3).inner_text() == expected, model["name"]
        assert scores == sorted(scores, reverse=True)
        assert page.locator('#ranking tr[data-model="seed-2.1"]').count() == 1
        assert page.locator('#ranking tr[data-model="seed-2.1-0915"], #ranking tr[data-model="seed-2.1-0902"]').count() == 0
        seed = next(m for m in data["models"] if m["name"] == "seed-2.1")
        assert seed["terrain"]["source_model"] == "seed-2.1-0915"
        assert seed["classification"]["source_model"] == "seed-2.1-0902"
        assert "同一模型" in page.locator("#version-note").inner_text()
        terrain_models = [m for m in data["models"] if m["terrain"]]
        point_checks = []
        for model in terrain_models:
            name = model["name"]
            page.select_option("#model", name)
            page.wait_for_selector('#prediction[aria-busy="false"]')
            assert page.locator("#selected-model").get_attribute("data-model") == name
            raw = page.request.get(args.url + model["terrain"]["details_url"]).json()
            invalid = next((r for r in raw.values() if r["status"] == "invalid"), None)
            sample = invalid["sample_id"] if invalid else 6596
            page.locator("#point").fill(str(sample))
            page.locator("#point").press("Tab")
            page.wait_for_timeout(60)
            assert page.locator("#answer").inner_text() == raw[str(sample)].get("text", "请求未决；无可用模型回答。")
            if model["classification"]:
                class_raw = page.request.get(args.url + model["classification"]["details_url"]).json()
                assert page.locator("#classification-answer").text_content() == class_raw[str(sample)].get("text", "请求未决；无可用模型回答。")
            else:
                assert "没有独立分类任务" in page.locator("#classification-point-note").text_content()
            point_checks.append({"model": name, "sample_id": sample})
        page.locator('#ranking tr[data-model="gpt-6-astra"] button').click()
        page.wait_for_selector('#prediction[aria-busy="false"]')
        assert page.locator("#model").input_value() == "gpt-6-astra"
        point = next(p for p in ref["points"] if p["height_reference_status"] != "ok")
        for ident in [0, point["id"], 16199, 6596]:
            page.locator("#point").fill(str(ident))
            page.locator("#point").press("Tab")
            page.wait_for_timeout(80)
            assert "#" + str(ident) in page.locator("#point-error").inner_text()
            if ident == point["id"]:
                assert "不可评分" in page.locator("#point-values").inner_text()
        page.locator("#reference").scroll_into_view_if_needed()
        before = page.locator("#reference").evaluate("(c)=>c.toDataURL()")
        rect = page.locator("#reference").bounding_box()
        x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
        page.mouse.move(x, y); page.mouse.down()
        page.mouse.move(x + 95, y + 20, steps=8); page.mouse.up()
        page.wait_for_timeout(100)
        assert before != page.locator("#reference").evaluate("(c)=>c.toDataURL()")
        layouts = []
        for width in [1440, 768, 390, 320]:
            page.set_viewport_size({"width": width, "height": 1080})
            page.evaluate("scrollTo(0,0)")
            page.wait_for_timeout(200)
            bounds = page.evaluate("({width:innerWidth,scrollWidth:document.documentElement.scrollWidth})")
            assert bounds["scrollWidth"] <= bounds["width"], bounds
            layouts.append(bounds)
            page.screenshot(path=str(args.output / f"page-{width}.png"), full_page=True)
            if width == 1440:
                page.screenshot(path=str(args.output / "desktop-viewport.png"))
        for path in ["config.json", "manifest.json", ".env", "../scripts/pipeline_config.yaml",
                     "gpt-6-1-sol.details.json", "terrain-gpt-6-1-sol.details.json", "classification-gpt-6-1-sol.details.json"]:
            assert page.request.get(args.url + path).status == 404, path
        assert not errors, errors
        proof = {"passed": True, "models": len(data["models"]), "terrain_models": len(terrain_models),
                 "classification_models": data["sources"]["classification"]["model_count"],
                 "task_columns_verified": True, "seed_aliases_merged": True, "source_aliases_preserved": True,
                 "point_checks": point_checks,
                 "reference_mask_checked": True, "rotation_checked": True, "layouts": layouts, "browser_errors": errors}
        (args.output / "verification.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(proof, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
