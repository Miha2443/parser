import type { CatalogDeveloper } from './types';

export function matchingDevelopers(developers: CatalogDeveloper[], query: string) {
  const normalize = (value: string) => value.trim().toLocaleLowerCase('ru-RU').replaceAll('ё', 'е');
  const words = normalize(query).split(/\s+/).filter(Boolean);
  return developers.filter(developer => words.every(word => normalize(developer.name).includes(word)));
}
