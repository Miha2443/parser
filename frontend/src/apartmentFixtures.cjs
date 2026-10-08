const roomTypes = ['1 комн', '2 комн', '3 комн', '4+ комн'];
const metadata = { schemaVersion: 1, version: 'apartments-v1', generatedAt: '2026-10-07T12:00:00Z', reportDate: '02.07.2026', source: { date: '06.07.2026', files: ['data/raw/apartments-candidate.json'], issues: [['warning', 'Неполные доли комнатности источника']] } };
const rooms = roomTypes.map((type, i) => ({ type, sharePercent: [30, 20, null, 10][i] }));
const developers = Array.from({ length: 73 }, (_, i) => ({ id: i === 72 ? 'СЗ "Тест" / А+Б' : `Девелопер ${i + 1}`, name: i === 72 ? 'СЗ "Тест" / А+Б' : `Девелопер ${i + 1}`, place: i + 1, apartmentThousandCount: i === 0 ? null : 4.25, areaThousandM2: 1000 - i, rooms }));
const rfDevelopers = [{ ...developers[1], id: 'РФ Компания', name: 'РФ Компания', place: 1 }, { ...developers[2], id: 'РФ Вторая', name: 'РФ Вторая', place: 2 }, { ...developers[1], place: 3 }];
const regions = [{ ...developers[0], id: 'Москва', name: 'Город Москва', place: undefined }, { ...developers[1], id: 'Регион 2', name: 'Регион 2', areaThousandM2: 9999, place: undefined }];
const catalog = { ...metadata, regions: [{ id: 'msk', label: 'Город Москва' }, { id: 'rf', label: 'Российская Федерация' }], developersByRegion: { msk: developers.map(({ id, name, place }) => ({ id, name, place })), rf: rfDevelopers.map(({ id, name, place }) => ({ id, name, place })) } };
function overview(region = 'msk', meta = metadata) {
  const rows = region === 'msk' ? developers : rfDevelopers;
  return { ...meta, region, apartments: [{ type: 'Все квартиры', count: 12345, areaThousandM2: 654.3 }, { type: 'Студии', count: null, areaThousandM2: 12.34 }, { type: '1-комнатные', count: 4000, areaThousandM2: null }], distribution: ['до 30', '30–40', '40–50', '50–60', '60–70', '70–80', '80–90', '90–100', '100–120', '120+'].map((range, i) => ({ range, sharePercent: i === 8 ? null : i + 1 })), developers: rows, regions, developerCount: rows.length, regionCount: regions.length };
}
function detail(region = 'msk', id = developers[0].id, meta = metadata) {
  const rows = region === 'msk' ? developers : rfDevelopers, developer = rows.find(r => r.id === id);
  if (!developer) return null;
  return { ...meta, region, developer, summary: { countThousand: 4.25, areaThousandM2: 250.5, averageAreaM2: 58.9, marketSharePercent: 3.25, marketBaseAreaThousandM2: 7707.7, place: developer.place, totalDevelopers: rows.length }, referenceAverages: [{ region: 'msk', averageAreaM2: null }, { region: 'rf', averageAreaM2: 49.5 }], rooms, comparison: rows.slice(0, 10).some(r => r.id === id) ? rows.slice(0, 10) : [...rows.slice(0, 10), developer] };
}
module.exports = { metadata, catalog, developers, rfDevelopers, regions, rooms, overview, detail };
