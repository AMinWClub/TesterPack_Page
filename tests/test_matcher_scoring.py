"""Run: python -m unittest tests.test_matcher_scoring -v"""
import unittest

from app.services.matcher.scoring import (
    dominant_gender, profile, rank_recommendations, similarity,
)


def perfume(*, gender='female', accord='citrus', note=77, warmth='neutral', strength=90):
    return {
        'genderVotes': {'votes': {gender: 100}},
        'mainAccords': [{'name': accord, 'strength': strength}],
        'notesTop': [{'noteId': note, 'name': 'Lemon', 'weight': 0.9}],
        'notesMiddle': [], 'notesBase': [],
        'olfactory_preferences': {'temperature': warmth, 'sweetness': 'medium', 'freshness': 'medium'},
    }


class ScoreTests(unittest.TestCase):
    def test_gender_is_strict_but_unisex_is_allowed_for_gendered_source(self):
        source = profile(perfume(gender='female'))
        self.assertEqual(dominant_gender(perfume(gender='female')), 'female')
        self.assertEqual(similarity(source, profile(perfume(gender='male'))), 0)
        self.assertGreater(similarity(source, profile(perfume(gender='unisex'))), 0)
        unisex = profile(perfume(gender='unisex'))
        self.assertEqual(similarity(unisex, profile(perfume(gender='female'))), 0)

    def test_missing_or_tied_gender_cannot_be_recommended(self):
        missing = perfume()
        missing.pop('genderVotes')
        self.assertIsNone(profile(missing))
        tied = perfume()
        tied['genderVotes']['votes'] = {'male': 10, 'female': 10}
        self.assertIsNone(profile(tied))

    def test_strength_and_note_weight_affect_similarity(self):
        src = profile(perfume())
        close = profile(perfume())
        far = profile(perfume(accord='woody', note=99, warmth='warm'))
        self.assertGreater(similarity(src, close), 0.75)
        self.assertEqual(similarity(src, far), 0)
        weak = perfume()
        weak['mainAccords'].append({'name': 'woody', 'strength': 100})
        self.assertLess(similarity(src, profile(weak)), similarity(src, close))

    def test_malformed_profiles_fail_closed_and_no_division_by_zero(self):
        rows = [{}, {'genderVotes': {'votes': {'female': 9}}},
                {'genderVotes': {'votes': {'female': 9}}, 'mainAccords': [{'name': 'sweet', 'strength': float('nan')}]}]
        for row in rows:
            self.assertIsNone(profile(row))

    def test_rank_uses_10_to_20_if_possible_without_duplicates_or_bought_products(self):
        src = profile(perfume())
        candidates = {str(i): profile(perfume()) for i in range(1, 30)}
        candidates['28'] = profile(perfume(gender='male'))
        output = rank_recommendations([src, src, src], candidates, {'1', '2'})
        self.assertEqual(len(output), 18)
        self.assertEqual(len(set(output)), 18)
        self.assertTrue(set(output).isdisjoint({'1', '2', '28'}))
        self.assertEqual(len(rank_recommendations([src], candidates, {'1', '2'})), 12)
        self.assertEqual(len(rank_recommendations([src, src], candidates, {'1', '2'})), 14)

    def test_do_not_fill_recommendations_with_incompatible_or_weak_items(self):
        src = profile(perfume())
        candidates = {'good': profile(perfume()), 'bad': profile(perfume(gender='male')),
                      'no-scent': profile(perfume(accord='woody', note=99))}
        self.assertEqual(rank_recommendations([src], candidates, set()), ['good'])


if __name__ == '__main__':
    unittest.main()
