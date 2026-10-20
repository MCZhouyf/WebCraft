import json
import unittest
from pathlib import Path
from conflict_core import recipe_assertion, accepts_layout

ROOT=Path('/root/autodl-tmp/webcraft')

class TruthTests(unittest.TestCase):
    def test_legacy_stonecutter_count(self):
        for v in ['1.16.5','1.19']:
            truth=json.loads((ROOT/f'truth/{v}.json').read_text())
            recipes=[r for r in truth['recipes'] if r['type']=='minecraft:stonecutting' and r['result']['item']=='minecraft:andesite_slab']
            self.assertTrue(recipes)
            for r in recipes: self.assertEqual(r['result']['count'],2); self.assertEqual(r['raw']['count'],2)

    def test_random_gravel_is_not_guaranteed_flint(self):
        for v in ['1.16.5','1.19','1.21.11']:
            truth=json.loads((ROOT/f'truth/{v}.json').read_text())
            drop=next(d for d in truth['drops'] if d['block']=='minecraft:gravel')
            self.assertEqual(drop['no_silk_touch_items'],['minecraft:flint','minecraft:gravel'])
            self.assertEqual(drop['set_semantics'],'possible_items_not_guaranteed_yields')

    def test_mirrored_recipe_canonical(self):
        a={'type':'minecraft:crafting_shaped','station':'crafting_grid','ordered':True,'pattern':['AB'],'key':{'A':['minecraft:stick'],'B':['minecraft:paper']},'result':{'item':'minecraft:name_tag','count':1}}
        b={**a,'pattern':['BA']}
        self.assertEqual(recipe_assertion(a),recipe_assertion(b))
        self.assertTrue(accepts_layout(recipe_assertion(a),['paper','stick',None,None,None,None,None,None,None]))

    def test_all_separators_and_universal_false_control(self):
        report=json.loads((ROOT/'reports/stage1_conflict_inventory.json').read_text())
        rows={r['separator_id']:r for r in report['separators']}
        self.assertEqual(len(rows),12)
        self.assertEqual(rows[2]['grade'],'C'); self.assertEqual(rows[9]['grade'],'C')
        self.assertFalse(rows[12]['is_version_conflict'])
        self.assertFalse(any(rows[12]['truth_vector'].values()))

if __name__=='__main__': unittest.main()
