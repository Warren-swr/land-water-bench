"""Validate the unified page against its saved data, without API calls."""
import argparse
import csv
import json
import math
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from check_model_comparison import check_model_comparison


def check_leaderboard(page, data, output):
    pct = lambda n: f"{100*n:.2f}%"
    assert page.locator("#ranking tr").count() == len(data["models"])
    assert "gpt-6-1-sol" not in page.locator("#ranking").inner_text()
    scores = []
    for model in data["models"]:
        cells = page.locator('#ranking tr[data-model="' + model["name"] + '"] td')
        score = model["terrain"]["metrics"]["joint_accuracy"]["500"]
        scores.append(score)
        assert cells.nth(1).inner_text().replace("\n", "") == pct(score)
        expected = pct(model["classification"]["metrics"]["weighted_accuracy"]) if model["classification"] else "—"
        assert cells.nth(3).inner_text() == expected, model["name"]
        assert cells.nth(4).inner_text() == pct(model["terrain"]["metrics"]["coverage"])
    assert scores == sorted(scores, reverse=True)
    page.locator('[data-family="claude"]').click()
    assert page.locator("#ranking tr").count() == 3
    page.locator("#model-search").fill("haiku")
    assert page.locator("#ranking tr").get_attribute("data-model") == "claude-haiku-5-5"
    page.locator("#model-search").fill("no matching model")
    assert page.locator("#empty-state").is_visible()
    page.locator("#reset-filters").click()
    assert page.locator("#ranking tr").count() == len(data["models"])
    with page.expect_download() as download_info:
        page.locator("#download-csv").click()
    download_info.value.save_as(str(output / "leaderboard.csv"))
    with (output / "leaderboard.csv").open(encoding="utf-8-sig") as file:
        exported = list(csv.DictReader(file))
    assert len(exported) == len(data["models"]) and exported[0]["model"] == data["models"][0]["name"]
    assert float(exported[0]["joint_accuracy_500"]) == scores[0]
    seed = next(m for m in data["models"] if m["name"] == "seed-2.1")
    assert seed["terrain"]["source_model"] == "seed-2.1-0915" and seed["classification"]["source_model"] == "seed-2.1-0902"
    assert page.locator('#ranking tr[data-model="seed-2.1-0915"], #ranking tr[data-model="seed-2.1-0902"]').count() == 0


def check_tolerance(page, data, atlas):
    slider = page.locator("#tolerance-slider")
    assert (slider.get_attribute("min"), slider.get_attribute("max"), slider.get_attribute("step")) == ("100", "2000", "100")
    assert atlas["tolerances_m"] == list(range(100, 2001, 100))
    for model in data["models"]:
        curve, official = atlas["models"][model["name"]]["tolerance_curve"], model["terrain"]["metrics"]["joint_accuracy"]
        for tolerance in (100, 500, 1000):
            assert math.isclose(curve[tolerance // 100 - 1], official[str(tolerance)], abs_tol=1e-12)
        assert curve == sorted(curve)
    rows = page.locator("#tolerance-ranking li")
    checked = []
    for value in (500, 1000, 2000, 100):
        slider.fill(str(value))
        page.wait_for_timeout(120)
        expect(page.locator("#tolerance-value")).to_have_text(f"±{value:,}")
        index = value // 100 - 1
        expected = sorted(data["models"], key=lambda m: (-atlas["models"][m["name"]]["tolerance_curve"][index], m["name"]))
        assert [rows.nth(i).get_attribute("data-model") for i in range(rows.count())] == [m["name"] for m in expected]
        top = atlas["models"][expected[0]["name"]]["tolerance_curve"][index]
        assert rows.nth(0).locator(".vl").inner_text() == f"{100*top:.1f}%"
        checked.append(value)
    slider.fill("500")
    slider.press("ArrowRight")
    expect(page.locator("#tolerance-value")).to_have_text("±600")
    slider.fill("2000")
    page.wait_for_timeout(100)
    assert "下降" in page.locator("#tolerance-insight").inner_text()
    page.locator('#tolerance-ranking li[data-model="gpt-6-astra"] .nm').click()
    expect(page.locator('#tolerance-ranking li[data-model="gpt-6-astra"]')).to_have_class("focus")
    slider.fill("500")
    return checked


def check_globes(page, data):
    hero = page.locator("#hero-globe")
    for canvas in ["#hero-globe", "#model-globe", "#abyss-globe"]:
        assert page.locator(canvas).get_attribute("data-webgl") == "ready", canvas
    page.evaluate("scrollTo({top:0,behavior:'instant'})")
    page.wait_for_timeout(300)
    before = hero.evaluate("c=>c.toDataURL()")
    page.wait_for_timeout(300)
    assert before != hero.evaluate("c=>c.toDataURL()")
    page.locator("#motion-toggle").click()
    page.wait_for_timeout(200)
    paused = hero.evaluate("c=>c.toDataURL()")
    page.wait_for_timeout(300)
    assert paused == hero.evaluate("c=>c.toDataURL()")
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_timeout(100)
    assert page.locator("#motion-toggle").get_attribute("aria-pressed") == "false"
    page.emulate_media(reduced_motion="no-preference")
    expect(page.locator("#motion-toggle")).to_have_attribute("aria-pressed", "true")

    globe = page.locator("#model-globe")
    globe.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    assert page.locator('[data-globe-mode="error"]').is_disabled()
    reference = globe.evaluate("c=>c.toDataURL()")
    page.locator('[data-globe-model="deepseek-v4.1-flash"]').click()
    page.wait_for_timeout(1500)
    expect(globe).to_have_attribute("data-model", "deepseek-v4.1-flash")
    terrain = globe.evaluate("c=>c.toDataURL()")
    assert terrain != reference
    page.locator('[data-globe-mode="error"]').click()
    page.wait_for_timeout(1500)
    expect(globe).to_have_attribute("data-mode", "error")
    assert globe.evaluate("c=>c.toDataURL()") != terrain
    deepseek = next(m for m in data["models"] if m["name"] == "deepseek-v4.1-flash")
    assert f"{100*deepseek['terrain']['metrics']['joint_accuracy']['500']:.2f}%" in page.locator("#globe-stats").inner_text()
    page.locator('[data-globe-model="reference"]').click()
    expect(globe).to_have_attribute("data-mode", "terrain")
    box = globe.bounding_box()
    page.locator("#point").fill("0")
    page.locator("#point").press("Tab")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(200)
    assert page.locator("#point").input_value() != "0"
    expect(page.locator("#globe-readout")).to_contain_text(page.locator("#point-title").inner_text())
    before = globe.evaluate("c=>c.toDataURL()")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 120, box["y"] + box["height"] / 2 + 20, steps=8)
    page.mouse.up()
    page.wait_for_timeout(200)
    assert before != globe.evaluate("c=>c.toDataURL()")


def check_abyss(page, data):
    steps = page.locator(".abyss-step")
    assert steps.count() == 5
    badges = []
    for i in range(steps.count()):
        steps.nth(i).scroll_into_view_if_needed()
        page.evaluate("(el)=>el.scrollIntoView({block:'center',behavior:'instant'})", steps.nth(i).element_handle())
        page.wait_for_timeout(250)
        expect(steps.nth(i)).to_have_class("abyss-step active")
        badges.append(page.locator("#abyss-depth").inner_text())
    assert badges[0] == "海平面 0 m" and badges[-1] == "海水已排干"
    shelf = steps.nth(1).locator(".step-bars li")
    assert shelf.count() == len(data["models"])
    rows = page.locator("#strata-matrix tbody tr")
    assert rows.count() == len(data["models"])
    first = data["models"][0]["terrain"]["metrics"]["by_stratum"]
    cells = rows.nth(0).locator("td")
    for i, key in enumerate(["solid_low", "solid_mid", "solid_high", "solid_alpine", "water_shelf", "water_slope", "water_deep", "water_abyssal"]):
        assert cells.nth(i).inner_text() == f"{100*first[key]['joint_accuracy']['500']:.1f}"
    return badges


def check_answers(page, data, ref, url):
    point_checks = []
    for model in data["models"]:
        name = model["name"]
        page.select_option("#model", name)
        expect(page.locator("#selected-model")).to_have_attribute("data-model", name)
        raw = page.request.get(url + model["terrain"]["details_url"]).json()
        invalid = next((r for r in raw.values() if r["status"] == "invalid"), None)
        sample = invalid["sample_id"] if invalid else 6596
        page.locator("#point").fill(str(sample))
        page.locator("#point").press("Tab")
        expect(page.locator("#answer")).to_have_text(raw[str(sample)].get("text", "请求未决；无可用模型回答。"))
        class_raw = page.request.get(url + model["classification"]["details_url"]).json()
        expect(page.locator("#classification-answer")).to_have_text(class_raw[str(sample)].get("text", "请求未决；无可用模型回答。"))
        point_checks.append({"model": name, "sample_id": sample})
    page.locator('#ranking tr[data-model="gpt-6-astra"] button').click()
    expect(page.locator("#model")).to_have_value("gpt-6-astra")
    expect(page.locator("#model-globe")).to_have_attribute("data-model", "gpt-6-astra")
    masked = next(p for p in ref["points"] if p["height_reference_status"] != "ok")
    for ident in [0, masked["id"], 16199, 6596]:
        page.locator("#point").fill(str(ident))
        page.locator("#point").press("Tab")
        expect(page.locator("#point-error")).to_contain_text("#" + str(ident))
        if ident == masked["id"]:
            assert "不可评分" in page.locator("#point-values").inner_text()
    return point_checks


def check_atlas(page, data, ref, url, output):
    page.locator("#atlas-map").scroll_into_view_if_needed()
    page.select_option("#model-a", "reference")
    page.select_option("#model", "gpt-6-astra")
    shot = lambda: page.locator("#atlas-map").evaluate("c=>c.toDataURL()")
    handle = page.locator("#swipe-handle")
    handle.press("Home")
    expect(handle).to_have_attribute("aria-valuenow", "0")
    all_b = shot()
    handle.press("End")
    expect(handle).to_have_attribute("aria-valuenow", "100")
    assert shot() != all_b
    for _ in range(10):
        handle.press("ArrowLeft")
    expect(handle).to_have_attribute("aria-valuenow", "50")
    height_map = shot()
    page.locator('[data-layer="error"]').click()
    page.wait_for_timeout(100)
    error_map = shot()
    assert height_map != error_map
    assert "联合错误" in page.locator("#map-legend").inner_text()
    page.locator('[data-layer="classification"]').click()
    page.wait_for_timeout(100)
    assert error_map != shot()
    assert "不是同一次回答" in page.locator("#plot-note").inner_text()
    sample = 3796
    rect = page.locator("#atlas-map").bounding_box()
    page.locator("#atlas-map").click(position={"x": (sample % 180 + .5) / 180 * rect["width"], "y": (sample // 180 + .5) / 90 * rect["height"]})
    expect(page.locator("#point")).to_have_value("3796")
    page.locator('[data-coordinate="47,-87"]').click()
    expect(page.locator("#point-title")).to_have_text("47° N / 87° W")
    assert "不可评分" in page.locator("#point-values").inner_text()
    page.locator(".coordinate-picker > summary").click()
    page.locator("#latitude").fill("33")
    page.locator("#longitude").fill("85")
    page.locator("#coordinate-form button").click()
    expect(page.locator("#point-title")).to_have_text("33° N / 85° E")
    page.locator("#error-example").click()
    page.wait_for_timeout(100)
    selected = int(page.locator("#point").input_value())
    astra = next(m for m in data["models"] if m["name"] == "gpt-6-astra")
    row = page.request.get(url + astra["terrain"]["details_url"]).json()[str(selected)]
    truth = ref["points"][selected]
    assert truth["elevation_m"] is not None
    assert row["status"] == "invalid" or row["label"] != truth["truth"] or abs(row["prediction"]["elevation_m"] - truth["elevation_m"]) > 500
    page.locator('[data-layer="elevation"]').click()
    page.locator("#explore").screenshot(path=str(output / "atlas-map.png"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8736/")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chromium")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True,
                                             args=["--no-sandbox", "--enable-unsafe-swiftshader", "--use-angle=swiftshader"])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.wait_for_selector('body[data-ready="true"]')
        data = page.request.get(args.url + "data.json").json()
        ref = page.request.get(args.url + "reference.details.json").json()
        atlas = page.request.get(args.url + "atlas.json").json()
        assert len(ref["points"]) == 16200 and set(atlas["models"]) == {m["name"] for m in data["models"]}
        check_leaderboard(page, data, args.output)
        tolerances = check_tolerance(page, data, atlas)
        check_globes(page, data)
        point_checks = check_answers(page, data, ref, args.url)
        check_atlas(page, data, ref, args.url, args.output)
        comparison_checks = check_model_comparison(page, data, ref, args.url, args.output, errors)
        badges = check_abyss(page, data)
        layouts = []
        for width in [1440, 768, 390, 320]:
            page.set_viewport_size({"width": width, "height": 1080})
            page.evaluate("scrollTo(0,0)")
            page.wait_for_timeout(250)
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
        proof = {"passed": True, "models": len(data["models"]), "point_checks": point_checks,
                 "tolerances_checked": tolerances, "abyss_steps": badges, "layouts": layouts, "browser_errors": errors,
                 "filters_checked": True, "csv_checked": True, "seed_aliases_merged": True, "globe_checks": True,
                 "map_layers_checked": True, "coordinate_navigation_checked": True, "model_comparison": comparison_checks}
        (args.output / "verification.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(proof, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
