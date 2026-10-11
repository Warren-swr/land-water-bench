"""Browser acceptance for the terrain pilot, using saved results only."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8734/')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chromium', default=None)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440, 'height':1080}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        response = page.goto(args.url)
        assert response.status == 200, 'page status'
        data = page.request.get(args.url + 'data.json').json()
        page.wait_for_selector('#ranking tr')
        models = sorted(data['models'], key=lambda m: -m['metrics']['joint_accuracy']['500'])
        assert page.locator('#ranking tr').count() == len(models), 'ranking count'
        assert models[0]['name'] in page.locator('#ranking tr').first.inner_text(), 'ranking order'
        assert page.locator('#point option').count() == len(data['points']), 'point selection'
        rendered = []
        for model in models:
            page.select_option('#model', model['name'])
            assert page.locator('#selected-model').inner_text() == model['name'], 'model heading'
            assert page.locator('#strata .bar').count() == 8, 'strata count'
            rendered.append(page.locator('#prediction').evaluate('(c) => c.toDataURL()'))
            for key, record in model['records'].items():
                if record['status'] == 'invalid':
                    page.select_option('#point', key)
                    assert page.locator('#answer').inner_text() == record['text'], 'invalid original answer'
                    assert 'invalid' in page.locator('#point-values').inner_text(), 'invalid status'
        assert len(set(rendered)) == len(models), 'different model geometry'
        page.select_option('#model', models[0]['name'])
        page.select_option('#point', '200')
        page.locator('#reference').scroll_into_view_if_needed()
        before = page.locator('#reference').evaluate('(c) => c.toDataURL()')
        rect = page.locator('#reference').bounding_box()
        page.mouse.move(rect['x']+rect['width']/2, rect['y']+rect['height']/2)
        page.mouse.down()
        page.mouse.move(rect['x']+rect['width']/2+100, rect['y']+rect['height']/2+20, steps=8)
        page.mouse.up()
        after = page.locator('#reference').evaluate('(c) => c.toDataURL()')
        assert before != after, 'rotation changes geometry'
        layouts = []
        for width in [1440,768,390,320]:
            page.set_viewport_size({'width':width, 'height':1000})
            page.wait_for_timeout(100)
            layout = page.evaluate('({width:innerWidth,scrollWidth:document.documentElement.scrollWidth})')
            assert layout['scrollWidth'] <= layout['width'], f'layout overflow: {layout}'
            layouts.append(layout)
            page.screenshot(path=str(args.output / f'page-{width}.png'), full_page=True)
        assert not errors, errors
        for path in ['config.json','manifest.json','.env','../scripts/pipeline_config.yaml']:
            assert page.request.get(args.url + path).status == 404, f'private path: {path}'
        result = {'passed':True, 'browser_errors':errors, 'ranking_descending':True,
                  'models_tested':[m['name'] for m in models], 'point_count':len(data['points']),
                  'invalid_answer_inspected':True, 'rotation':True, 'layouts':layouts, 'private_paths_blocked':True}
        (args.output / 'verification.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result))
        browser.close()


if __name__ == '__main__':
    main()
