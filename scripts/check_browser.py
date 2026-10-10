"""Browser acceptance for the presentation-only benchmark leaderboard."""

import argparse
import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright


def verify_maps(page, data, mode):
    selected = page.evaluate("() => [modelA, modelB]")
    by_name = {m['name']: m for m in data['models']}
    a, b = [data['truth'] if name == 'reference' else by_name[name]['pixels'] for name in selected]
    expected = []
    for name, pixels in zip(selected, [a, b]):
        values = []
        for i, value in enumerate(pixels):
            if value == '2': color = 'b08536'
            elif value not in '01': color = '997ab9'
            elif name == 'reference' or mode == 'map': color = '596cd8' if value == '1' else 'edf1fa'
            elif mode == 'error':
                color = ('909cb9' if value == '1' else 'edf1fa') if value == data['truth'][i] else ('e97b42' if value == '1' else '8b64c0')
            else:
                color = 'f0a552' if a[i] in '01' and b[i] in '01' and a[i] != b[i] else '909cb9' if value == '1' else 'edf1fa'
            values.append(color)
        expected.append(values)
    result = page.evaluate("""expected => ['a','b'].map((key, panel) => {
      const raster = document.getElementById('map-' + key).getContext('2d').getImageData(0,0,1080,540).data;
      for(let id=0;id<16200;id++){
        const offset=((Math.floor(id/180)*6+3)*1080+(id%180)*6+3)*4;
        const actual=Array.from(raster.slice(offset,offset+3)).map(x=>x.toString(16).padStart(2,'0')).join('');
        if(actual!==expected[panel][id]) return {panel:key,id,actual,expected:expected[panel][id]};
      }
      return {panel:key,verified:16200};
    })""", expected)
    assert all(r.get('verified') == 16200 for r in result), result


def inspect(page, sample_id):
    page.locator('.coordinate-picker').evaluate("e => e.open = true")
    page.locator('#latitude').fill(str(89 - sample_id // 180 * 2))
    page.locator('#longitude').fill(str(-179 + sample_id % 180 * 2))
    page.locator('#coordinate-form button').click()
    expect(page.locator('#point-answers .answer-row')).to_have_count(2)


def check(url, output):
    output.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440,'height':1000}, reduced_motion='reduce')
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='networkidle')
        page.wait_for_selector('body[data-ready=true]')
        page.evaluate('() => document.fonts.ready')
        data = page.evaluate('() => data')
        assert len(data['models']) == 12
        assert 'gpt-6-1-sol' not in json.dumps(data)
        assert 'GPT-6.1 Sol' not in page.locator('body').inner_text()
        order = [m['name'] for m in sorted(data['models'],key=lambda m:(-m['metrics']['weighted_accuracy'],m['name']))]
        assert page.locator('#ranking-body tr').evaluate_all("rows => rows.map(e => e.dataset.model)") == order
        assert page.locator('#model-count').inner_text() == '12'
        for i, model in enumerate(sorted(data['models'],key=lambda m:-m['metrics']['weighted_accuracy'])):
            assert page.locator('.score-value').nth(i).inner_text() == f"{model['metrics']['weighted_accuracy']:.2%}"
        assert '每模型独立并发' not in page.locator('body').inner_text()
        assert '本轮重试' not in page.locator('body').inner_text()
        assert page.locator('body').evaluate('e => e.scrollWidth') <= 1440
        checks.append('12 models only; removed model absent; exact scores and descending rank order; execution clutter removed')
        page.screenshot(path=str(output/'desktop.png'))
        page.screenshot(path=str(output/'desktop-full.png'), full_page=True)
        for family, count in [('GPT',3),('Claude',3),('other',6),('all',12)]:
            page.locator(f'[data-family="{family}"]').click()
            assert page.locator('#ranking-body tr').count() == count
        page.keyboard.press('/')
        expect(page.locator('#model-search')).to_be_focused()
        page.locator('#model-search').fill('terra')
        assert page.locator('#ranking-body tr').count() == 1
        assert page.locator('#ranking-body tr').get_attribute('data-model') == 'gpt-5.6-terra'
        page.locator('#model-search').fill('no-such-model')
        expect(page.locator('#empty-state')).to_be_visible()
        page.locator('#reset-filters').click()
        assert page.locator('#ranking-body tr').count() == 12
        with page.expect_download() as event:
            page.locator('#download-csv').click()
        download=event.value
        download.save_as(output/'leaderboard.csv')
        rows=list(csv.reader(io.StringIO((output/'leaderboard.csv').read_text(encoding='utf-8-sig'))))
        assert len(rows)==13 and [r[1] for r in rows[1:]]==order
        for row, model in zip(rows[1:], sorted(data['models'],key=lambda m:-m['metrics']['weighted_accuracy'])):
            assert row[2]==f"{model['metrics']['weighted_accuracy']*100:.4f}"
        assert 'gpt-6-1-sol' not in (output/'leaderboard.csv').read_text()
        checks.append('family filters, keyboard search, empty state, reset and CSV export with exact scores')
        for mode in ['map','error','difference']:
            page.locator(f'[data-mode="{mode}"]').click()
            page.mouse.move(0,0)
            verify_maps(page,data,mode)
        checks.append('all 16,200 pixels in both maps independently verified in all three display modes')
        page.locator('[data-inspect="gpt-5.6-terra"]').first.click()
        assert page.locator('#model-a').input_value()=='gpt-5.6-terra'
        page.locator('#model-b').select_option('reference')
        assert page.locator('#score-b').inner_text()=='REF'
        page.locator('[data-mode="map"]').click()
        page.mouse.move(0,0)
        verify_maps(page,data,'map')
        page.locator('#model-a').select_option('gpt-6-astra')
        page.locator('#model-b').select_option('claude-opus-5-5')
        page.locator('#atlas').screenshot(path=str(output/'atlas.png'))
        page.locator('#lake-example').click()
        expect(page.locator('.answer-row')).to_have_count(2)
        assert 'Water' in page.locator('#point-truth').inner_text()
        assert 'Lake Superior' in page.locator('#point-reference').inner_text()
        rows=page.evaluate("async () => await Promise.all([modelA,modelB].map(async n => (await getDetails(n))[3826]))")
        assert page.locator('.answer-row code').all_text_contents()==[r['text'] for r in rows]
        page.locator('#all-answers').click()
        expect(page.locator('.answer-row')).to_have_count(12)
        assert 'gpt-6-1-sol' not in page.locator('#point-answers').inner_html()
        page.screenshot(path=str(output/'answers.png'))
        page.keyboard.press('Escape')
        expect(page.locator('#point-dialog')).not_to_be_visible()
        page.locator('#reference-example').click()
        expect(page.locator('.answer-row')).to_have_count(2)
        assert '存在分歧' in page.locator('#point-reference').inner_text()
        page.locator('#close-dialog').click()
        page.locator('#model-a').select_option('kimi-k3')
        unknown=next(m for m in data['models'] if m['name']=='kimi-k3')['pixels'].index('3')
        inspect(page,unknown)
        article=page.locator('.answer-row[data-model="kimi-k3"]')
        assert '未决 · 不计分' in article.inner_text()
        assert '没有有效标签' in article.inner_text()
        page.locator('#close-dialog').click()
        page.locator('#model-a').select_option('mimo-v2.6-pro')
        invalid=next(m for m in data['models'] if m['name']=='mimo-v2.6-pro')['pixels'].index('2')
        inspect(page,invalid)
        assert '格式无效 · 计错' in page.locator('.answer-row[data-model="mimo-v2.6-pro"]').inner_text()
        page.locator('#close-dialog').click()
        page.locator('#model-a').select_option('claude-sonnet-5-5')
        example=page.evaluate("async () => Object.values(await getDetails('claude-sonnet-5-5')).find(r=>r.text?.length>180)")
        inspect(page,example['sample_id'])
        article=page.locator('.answer-row[data-model="claude-sonnet-5-5"]')
        article.locator('summary').click()
        assert article.locator('code').text_content()==example['text']
        page.locator('#close-dialog').click()
        checks.append('row-to-map navigation, reference option, exact original answers, all-model disclosure, reference uncertainty, unknown and invalid semantics')
        assert page.locator('#motion-toggle').get_attribute('aria-pressed')=='false'
        canvas_before=page.locator('#globe').evaluate('e=>e.toDataURL()')
        page.wait_for_timeout(150)
        assert page.locator('#globe').evaluate('e=>e.toDataURL()')==canvas_before
        page.locator('.coordinate-picker').evaluate('e=>e.open=false')
        page.locator('#model-a').select_option('gpt-6-astra')
        page.locator('#model-b').select_option('claude-opus-5-5')
        for width in [1280,768,390,360]:
            page.set_viewport_size({'width':width,'height':844})
            assert page.locator('body').evaluate('e=>e.scrollWidth')<=width
            page.evaluate('() => window.scrollTo(0,0)')
            if width==390:
                page.screenshot(path=str(output/'mobile.png'))
                page.screenshot(path=str(output/'mobile-full.png'),full_page=True)
                page.locator('#atlas').screenshot(path=str(output/'mobile-atlas.png'))
        checks.append('1280, 768, 390 and 360 px layouts without horizontal overflow; reduced-motion globe stays still')
        animated=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='no-preference')
        animated.on('pageerror',lambda error:errors.append(str(error)))
        animated.goto(url,wait_until='networkidle')
        animated.wait_for_selector('body[data-ready=true]')
        before=animated.locator('#globe').evaluate('e=>e.toDataURL()')
        animated.wait_for_timeout(200)
        assert animated.locator('#globe').evaluate('e=>e.toDataURL()')!=before
        animated.locator('#motion-toggle').click()
        before=animated.locator('#globe').evaluate('e=>e.toDataURL()')
        animated.wait_for_timeout(200)
        assert animated.locator('#globe').evaluate('e=>e.toDataURL()')==before
        checks.append('real data globe animates and pause control stops drawing')
        assert not errors,errors
        browser.close()
    for path in ['gpt-6-1-sol.details.json','manifest.json','../.env','.runner.lock','gpt-5.6-sol.jsonl']:
        for method in ['GET','HEAD']:
            try:
                urlopen(Request(url.rstrip('/')+'/'+path,method=method),timeout=10)
                raise AssertionError('Unexpected resource exposed: '+path)
            except HTTPError as error:
                assert error.code in {403,404}
    checks.append('removed model, raw responses, manifests and private files unavailable by GET or HEAD')
    result={'passed':True,'verified_at':datetime.now(timezone.utc).isoformat(),'url':url,'run_id':data['run_id'],'models':order,'checks':checks,'console_errors':errors}
    (output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    check(args.url,args.output)
