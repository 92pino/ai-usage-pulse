import unittest

from scripts.install_readme import END, START, card_block, install


class ReadmeInstallTests(unittest.TestCase):
    def test_first_install_preserves_existing_readme(self):
        result = install('# Profile\n\nHello\n',card_block('combo'),'## AI Coding Activity')
        self.assertTrue(result.startswith('# Profile\n\nHello'))
        self.assertEqual(result.count(START),1)
        self.assertIn('ai-usage-combo-dark.svg',result)
        self.assertIn('width="900"',result)

    def test_reinstall_replaces_only_managed_block(self):
        first = install('# Profile\n',card_block('compact'),'## Usage')
        second = install(first,card_block('full'),'## Ignored')
        self.assertEqual(second.count(START),1)
        self.assertEqual(second.count(END),1)
        self.assertIn('ai-usage-full-dark.svg',second)
        self.assertNotIn('ai-usage-compact-dark.svg',second)
        self.assertEqual(second.count('## Usage'),1)

    def test_split_uses_both_half_width_cards(self):
        block = card_block('split')
        self.assertIn('ai-usage-half-dark.svg',block)
        self.assertIn('ai-usage-grass-dark.svg',block)
        self.assertEqual(block.count('width="49%"'),2)

    def test_broken_markers_fail_without_overwriting(self):
        with self.assertRaises(ValueError):
            install(f'# Profile\n{START}\n','block','## Usage')


if __name__ == '__main__':
    unittest.main()
