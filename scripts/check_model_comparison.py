"""Check paired maps against frozen answers, including missing data and async changes."""
import math

from playwright.sync_api import expect


def check_model_comparison(page, data, ref, url, output, errors):
    models = {model["name"]: model for model in data["models"]}
    raw = {}
    for name in ["gpt-6-astra", "mimo-v2.6-pro"]:
        raw[name] = page.request.get(url + models[name]["terrain"]["details_url"]).json()
    raw["reference"] = {str(p["id"]): {"status": "ok", "label": p["truth"],
                         "prediction": {"elevation_m": p["elevation_m"]}} for p in ref["points"]}

    def wait():
        page.wait_for_selector('#reference[aria-busy="false"]')
        page.wait_for_selector('#prediction[aria-busy="false"]')

    def choose(a, b):
        page.select_option("#model-a", a)
        page.select_option("#model", b)
        wait()

    def check_counts(a, b):
        classification = []
        heights = []
        for key, left in raw[a].items():
            right = raw[b][key]
            if left["status"] != "ok" or right["status"] != "ok":
                continue
            classification.append(left["label"] != right["label"])
            x, y = left["prediction"]["elevation_m"], right["prediction"]["elevation_m"]
            if x is not None and y is not None and math.isfinite(x) and math.isfinite(y):
                heights.append(abs(y - x))
        actual = page.locator("#pair-summary").evaluate("e=>({...e.dataset})")
        assert int(actual["classComparable"]) == len(classification)
        assert int(actual["classDifferent"]) == sum(classification)
        assert int(actual["heightComparable"]) == len(heights)
        assert int(actual["heightDifferent"]) == sum(delta > 500 for delta in heights)
        assert math.isclose(float(actual["heightMean"]), sum(heights) / len(heights), abs_tol=1e-7)
        return actual

    def inspect(ident):
        page.locator("#point").fill(str(ident))
        page.locator("#point").press("Tab")
        expect(page.locator("#point-error")).to_contain_text("#" + str(ident))

    def pixel(canvas, ident):
        return page.locator(canvas).evaluate("""(c,id)=>Array.from(c.getContext('2d').getImageData(
            Math.floor((id%180+.5)/180*c.width),Math.floor((Math.floor(id/180)+.5)/90*c.height),1,1).data)""", ident)

    choose("gpt-6-astra", "mimo-v2.6-pro")
    counts = check_counts("gpt-6-astra", "mimo-v2.6-pro")
    assert int(counts["classComparable"]) < 16200
    page.locator('[data-view="map"]').click()
    page.locator('[data-layer="class-diff"]').click()
    page.wait_for_timeout(100)
    disagreement = next(int(key) for key, a in raw["gpt-6-astra"].items()
                        if a["status"] == raw["mimo-v2.6-pro"][key]["status"] == "ok"
                        and a["label"] != raw["mimo-v2.6-pro"][key]["label"])
    for canvas, name in [("#reference", "gpt-6-astra"), ("#prediction", "mimo-v2.6-pro")]:
        color = pixel(canvas, disagreement)
        assert (color[0] > color[2]) == bool(raw[name][str(disagreement)]["label"])
    page.locator("#difference-example").click()
    expect(page.locator("#point-delta")).to_have_attribute("data-class-different", "true")
    point_id = int(page.locator("#point").input_value())
    expect(page.locator("#answer-a")).to_have_text(raw["gpt-6-astra"][str(point_id)]["text"])
    expect(page.locator("#answer")).to_have_text(raw["mimo-v2.6-pro"][str(point_id)]["text"])

    page.locator('[data-layer="height-diff"]').click()
    page.wait_for_timeout(100)
    height_id = next(int(key) for key, a in raw["gpt-6-astra"].items()
                     if a["status"] == raw["mimo-v2.6-pro"][key]["status"] == "ok"
                     and abs(a["prediction"]["elevation_m"] - raw["mimo-v2.6-pro"][key]["prediction"]["elevation_m"]) > 1000
                     and int(key) != point_id)
    delta = raw["mimo-v2.6-pro"][str(height_id)]["prediction"]["elevation_m"] - raw["gpt-6-astra"][str(height_id)]["prediction"]["elevation_m"]
    left, right = pixel("#reference", height_id), pixel("#prediction", height_id)
    assert (left[0] > left[2]) == (delta < 0)
    assert (right[0] > right[2]) == (delta > 0)
    inspect(height_id)
    assert math.isclose(float(page.locator("#point-delta").get_attribute("data-height-delta")), delta, abs_tol=1e-9)
    page.locator("#explore").screenshot(path=str(output / "model-pair-difference.png"))
    for width in [390, 320]:
        page.set_viewport_size({"width": width, "height": 1080})
        page.wait_for_timeout(100)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#explore").screenshot(path=str(output / f"model-pair-{width}.png"))
    page.set_viewport_size({"width": 1440, "height": 1080})
    page.locator("#swap-models").click()
    wait()
    assert page.locator("#model-a").input_value() == "mimo-v2.6-pro"
    assert page.locator("#model").input_value() == "gpt-6-astra"
    check_counts("mimo-v2.6-pro", "gpt-6-astra")
    assert math.isclose(float(page.locator("#point-delta").get_attribute("data-height-delta")), -delta, abs_tol=1e-9)

    unresolved = next(int(key) for key, row in raw["mimo-v2.6-pro"].items() if row["status"] == "infra_error")
    inspect(unresolved)
    expect(page.locator("#point-delta")).to_have_attribute("data-height-delta", "")
    expect(page.locator("#point-delta")).to_contain_text("无法比较")
    masked = next(p["id"] for p in ref["points"] if p["elevation_m"] is None
                  and raw["mimo-v2.6-pro"][str(p["id"])]["status"] == raw["gpt-6-astra"][str(p["id"])]["status"] == "ok")
    inspect(masked)
    assert page.locator("#point-delta").get_attribute("data-height-delta") != ""
    expect(page.locator("#point-values")).to_contain_text("不可评分")
    choose("gpt-6-astra", "reference")
    check_counts("gpt-6-astra", "reference")
    expect(page.locator("#focus-model")).to_have_text("A · GPT-6 Astra")
    expect(page.locator("#point-delta")).to_have_attribute("data-height-delta", "")
    choose("gpt-6-astra", "gpt-6-astra")
    same = check_counts("gpt-6-astra", "gpt-6-astra")
    assert same["classDifferent"] == same["heightDifferent"] == "0"
    assert page.locator("#difference-example").is_disabled()
    choose("reference", "reference")
    reference_pair = check_counts("reference", "reference")
    assert int(reference_pair["heightComparable"]) == data["height_scorable"]

    # A delayed old selection must not replace a newer selection or corrupt side B.
    race = page.context.new_page()
    race.on("pageerror", lambda error: errors.append(str(error)))
    race.goto(url)
    race.wait_for_selector('#prediction[aria-busy="false"]')
    held = []
    race.route("**/terrain-claude-opus-5-5.details.json", lambda route: held.append(route))
    race.select_option("#model-a", "claude-opus-5-5")
    race.wait_for_timeout(150)
    assert held
    race.select_option("#model-a", "kimi-k3")
    race.wait_for_selector('#reference[aria-busy="false"]')
    for route in held:
        route.continue_()
    race.wait_for_load_state("networkidle")
    assert race.locator("#reference").get_attribute("data-model") == "kimi-k3"
    expect(race.locator("#answer-title-a")).to_have_text("A · Kimi K3")
    assert race.locator("#prediction").get_attribute("data-model") == "gpt-6-astra"
    before = race.locator("#prediction").evaluate("c=>c.toDataURL()")
    race.route("**/terrain-gpt-5.6-terra.details.json", lambda route: route.abort())
    race.select_option("#model-a", "gpt-5.6-terra")
    race.wait_for_selector('#reference[aria-busy="false"]')
    assert race.locator("#pair-summary").get_attribute("data-class-comparable") is None
    expect(race.locator("#point-delta")).to_contain_text("无法比较")
    assert before == race.locator("#prediction").evaluate("c=>c.toDataURL()")
    race.select_option("#model-a", "reference")
    race.wait_for_selector('#reference[aria-busy="false"]')
    assert race.locator("#pair-summary").get_attribute("data-class-comparable") is not None
    race.close()

    choose("reference", "gpt-6-astra")
    page.locator('[data-layer="elevation"]').click()
    return {"pair_counts_verified": counts, "difference_colors_verified": True, "swap_verified": True,
            "missing_answers_excluded": True, "reference_mask_not_used_for_model_pair": True,
            "same_model_verified": True, "both_reference_verified": True,
            "stale_load_ignored": True, "load_error_isolated": True}
