import assert from 'node:assert/strict';
import test from 'node:test';
import { matchingDevelopers } from './developerSelectData.ts';

test('developer search keeps source construction rank and accepts case, spaces and yo', () => {
  const developers = [{ id: 'b', name: 'Большой застройщик', regions: ['msk'] }, { id: 'a', name: 'Альфа строй', regions: ['msk'] }, { id: 'r', name: 'Фонд Реновации', regions: ['msk'] }, { id: 'e', name: 'Берёзка Строй', regions: ['msk'] }];
  const before = structuredClone(developers);
  assert.deepEqual(matchingDevelopers(developers, ''), developers);
  assert.deepEqual(matchingDevelopers(developers, 'СТРОЙ').map(row => row.id), ['b', 'a', 'e']);
  assert.deepEqual(matchingDevelopers(developers, '  фонд   рено ').map(row => row.id), ['r']);
  assert.deepEqual(matchingDevelopers(developers, 'березка').map(row => row.id), ['e']);
  assert.deepEqual(matchingDevelopers(developers, 'несуществующий'), []);
  assert.deepEqual(developers, before);
});
