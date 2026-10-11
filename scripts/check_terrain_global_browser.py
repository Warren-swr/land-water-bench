"""Exercise the global page against real, already-verified result files without model calls."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8734/')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--chromium')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    errors=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(args.url)
        page.wait_for_selector('#ranking tr')
        assert 'gpt-6-1-sol' not in page.locator('#ranking').inner_text()
        assert '16,200' in page.locator('#reference-coverage').inner_text()
        available=page.locator('#model option').evaluate_all('(options)=>options.map(o=>o.value).filter(Boolean)')
        images=[]
        for name in available:
            page.select_option('#model',name)
            page.wait_for_selector('#prediction[aria-busy="false"]')
            page.wait_for_timeout(100)
            assert page.locator('#selected-model').inner_text()==name
            images.append(page.locator('#prediction').evaluate('(c)=>c.toDataURL()'))
            raw=page.request.get(args.url+name+'.details.json').json()
            selected=next((r for r in raw.values() if r['status']=='invalid'),None)
            if selected:
                page.locator('#point').fill(str(selected['sample_id']));page.locator('#point').press('Tab');page.wait_for_timeout(100)
                assert page.locator('#answer').inner_text()==selected['text']
        if len(images)>1: assert len(set(images))==len(images)
        for ident in [0,6596,16199]:
            page.locator('#point').fill(str(ident));page.locator('#point').press('Tab');page.wait_for_timeout(100)
            assert f'#{ident}' in page.locator('#point-error').inner_text()
        ref=page.request.get(args.url+'reference.details.json').json()
        assert len(ref['points'])==16200
        p=next(p for p in ref['points'] if p['height_reference_status']!='ok')
        page.locator('#point').fill(str(p['id']));page.locator('#point').press('Tab');page.wait_for_timeout(100)
        assert '不可评分' in page.locator('#point-values').inner_text()
        page.locator('#point').fill('6596');page.locator('#point').press('Tab')
        page.locator('#reference').scroll_into_view_if_needed()
        before=page.locator('#reference').evaluate('(c)=>c.toDataURL()')
        rect=page.locator('#reference').bounding_box()
        page.mouse.move(rect['x']+rect['width']/2,rect['y']+rect['height']/2)
        page.mouse.down();page.mouse.move(rect['x']+rect['width']/2+100,rect['y']+rect['height']/2+20,steps=8);page.mouse.up();page.wait_for_timeout(100)
        assert before!=page.locator('#reference').evaluate('(c)=>c.toDataURL()')
        layouts=[]
        for width in [1440,768,390,320]:
            page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(250)
            bounds=page.evaluate('({width:innerWidth,scrollWidth:document.documentElement.scrollWidth})')
            assert bounds['scrollWidth']<=bounds['width'],bounds
            layouts.append(bounds);page.screenshot(path=str(args.output/f'page-{width}.png'),full_page=True)
        assert not errors,errors
        for path in ['config.json','manifest.json','.env','../scripts/pipeline_config.yaml','gpt-6-1-sol.details.json']:
            assert page.request.get(args.url+path).status==404,path
        result={'passed':True,'models_tested':available,'global_points':16200,'reference_mask_checked':True,
                'invalid_bodies_checked':True,'rotation_checked':True,'private_and_hidden_paths_blocked':True,
                'layouts':layouts,'browser_errors':errors}
        (args.output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
        browser.close()


if __name__=='__main__':main()
