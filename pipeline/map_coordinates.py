"""Existing coordinate helpers, copied mechanically; no I/O defaults or UI dependencies."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
import pandas as pd

def coord_source_priority(source: object) -> int:
    text = '' if pd.isna(source) else str(source).strip().lower()
    if text.startswith('manual_exact'):
        return 100
    if text.startswith('geometry'):
        return 80
    if text.startswith('import'):
        return 70
    if text in {'yandex', 'nominatim'}:
        return 60
    if 'centroid' in text:
        return 10
    if text:
        return 50
    return 0

def clean_text(value: object) -> str:
    if isinstance(value, bool):
        return ''
    if value is None or pd.isna(value):
        return ''
    return re.sub('\\s+', ' ', str(value)).strip()

def address_key(value: object) -> str:
    text = clean_text(value).lower().replace('ё', 'е')
    text = re.sub('[\\"\'`«»]', '', text)
    text = text.replace('з/у', 'зу')
    text = re.sub('\\b(ул|д|вл|стр|корп|к|уч|зу|пос|дер|пр|пер|ш)\\.', '\\1', text)
    text = re.sub('\\bу\\.?\\s*(\\d)', 'уч \\1', text)
    text = re.sub('\\bуч\\.?\\s*(\\d)', 'уч \\1', text)
    text = re.sub('\\bзу\\s*(\\d)', 'зу \\1', text)
    text = re.sub('[,;]+', ' ', text)
    text = re.sub('\\s+', ' ', text)
    return text

def address_match_key(value: object) -> str:
    text = address_key(value)
    if not text:
        return ''
    text = re.sub('\\b(г|город)\\s+москва\\b', ' ', text)
    text = re.sub('\\bвнутригородская территория\\b', ' ', text)
    text = re.sub('\\bмуниципальный округ\\b', ' ', text)
    text = re.sub('\\b(поселение|пос)\\s+', ' ', text)
    text = re.sub('\\b(деревня|дер|д)\\s+(?=[а-я])', ' ', text)
    text = re.sub('\\b(село|с)\\s+(?=[а-я])', ' ', text)
    text = re.sub('\\bземельный участок\\b', ' уч ', text)
    text = re.sub('\\b(участок|уч|у|зу|владение|вл)\\s*(?=\\d)', ' уч ', text)
    text = re.sub('\\b(улица|ул)\\s+', 'ул ', text)
    text = re.sub('\\b(корпус|корп|к)\\s*(?=\\d)', ' корп ', text)
    text = re.sub('\\b(строение|стр|с)\\s*(?=\\d)', ' стр ', text)
    text = re.sub('\\bдом\\s*(?=\\d)', 'д ', text)
    text = re.sub('\\s+', ' ', text).strip()
    return text

def permit_key(value: object) -> str:
    text = clean_text(value).upper().replace(' ', '')
    return re.sub('[^\\wА-ЯЁ/-]+', '', text)

def _iter_coordinate_pairs(value: Any):
    if isinstance(value, list):
        if len(value) >= 2 and all((isinstance(x, (int, float)) for x in value[:2])):
            lon, lat = (float(value[0]), float(value[1]))
            if 35 <= lon <= 40 and 54 <= lat <= 57:
                yield (lon, lat)
            return
        for item in value:
            yield from _iter_coordinate_pairs(item)

def geometry_center(geometry_value: object) -> tuple[float | None, float | None]:
    text = clean_text(geometry_value)
    if not text or text.lower() in {'nan', 'none'}:
        return (None, None)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return (None, None)
    geometry = payload.get('geometry') if isinstance(payload, dict) else None
    coords = geometry.get('coordinates') if isinstance(geometry, dict) else None
    pairs = list(_iter_coordinate_pairs(coords))
    if not pairs:
        return (None, None)
    lon_values = [p[0] for p in pairs]
    lat_values = [p[1] for p in pairs]
    return ((min(lat_values) + max(lat_values)) / 2, (min(lon_values) + max(lon_values)) / 2)

def _first_existing(row: pd.Series, *cols: str) -> str:
    for col in cols:
        if col in row.index:
            value = clean_text(row.get(col))
            if value:
                return value
    return ''

def _num(value: object) -> float:
    try:
        out = float(pd.to_numeric(value, errors='coerce'))
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(out) else out

def _aggregate_registry(df: pd.DataFrame, registry: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    if 'УИН' in df.columns:
        object_ids = df['УИН'].map(clean_text)
        fallback_ids = pd.Series([f'{registry}_{i}' for i in range(len(df))], index=df.index)
        df['__object_id'] = object_ids.where(object_ids.ne(''), fallback_ids)
    else:
        df['__object_id'] = [f'{registry}_{i}' for i in range(len(df))]
    id_col = '__object_id'
    rows: list[dict] = []
    for object_id, group in df.groupby(id_col, dropna=False):
        object_id_text = clean_text(object_id) or f'{registry}_{len(rows)}'
        first = group.iloc[0]
        is_oks = registry == 'oks'
        address = _first_existing(first, 'Строительный адрес', 'Адрес')
        status_object = _first_existing(first, 'Статус объекта')
        commissioning = _first_existing(first, 'Ввод в эксплуатацию')
        if is_oks:
            status = 'Введено' if 'введ' in status_object.lower() or 'введ' in commissioning.lower() else 'Строится'
            year = _num(first.get('Год ввода по графику'))
            geometry = next((clean_text(v) for v in group.get('Геометрия', pd.Series(dtype=object)) if clean_text(v)), '')
            lat, lon = geometry_center(geometry)
        else:
            status = 'Введено'
            year = _num(first.get('Год ввода по Мосстату'))
            lat, lon = (None, None)
        rows.append({'registry': registry, 'object_id': object_id_text, 'source_sheet': _first_existing(first, 'source_sheet'), 'status': status, 'permit': _first_existing(first, '№РС', 'Разрешение на строительство'), 'permit_key': permit_key(_first_existing(first, '№РС', 'Разрешение на строительство')), 'developer': _first_existing(first, 'Группа компаний', 'Застройщик'), 'builder': _first_existing(first, 'Застройщик'), 'object_name': _first_existing(first, 'Коммерческое название', 'Коммерческое наименование', 'Наименование объекта'), 'address': address, 'address_key': address_key(address), 'address_match_key': address_match_key(address), 'okrug': _first_existing(first, 'Округ'), 'district': _first_existing(first, 'Район'), 'year': int(year) if year and (not pd.isna(year)) else None, 'area_total': sum((_num(v) for v in group.get('Общая площадь', pd.Series(dtype=object)))), 'area_living': sum((_num(v) for v in group.get('Жилая площадь', pd.Series(dtype=object)))), 'apartments': sum((_num(v) for v in group.get('Количество квартир', pd.Series(dtype=object)))), 'lat': lat, 'lon': lon, 'coord_source': 'geometry' if lat is not None and lon is not None else '', 'precision': ''})
    return pd.DataFrame(rows)

def apply_local_geometry(objects: pd.DataFrame, geometry_source: pd.DataFrame | None=None) -> pd.DataFrame:
    """Reuse OKS geometry for RV objects by permit number or exact address."""
    if objects.empty or 'registry' not in objects.columns:
        return objects
    out = objects.copy()
    source = out if geometry_source is None else geometry_source
    if source.empty or 'registry' not in source.columns:
        return out
    has_coords = source['lat'].notna() & source['lon'].notna()
    oks_geo = source[source['registry'].eq('oks') & has_coords & source['coord_source'].eq('geometry')].copy()
    if oks_geo.empty:
        return out

    def fill_from_lookup(key_col: str, source_label: str) -> None:
        nonlocal out
        if key_col not in out.columns or key_col not in oks_geo.columns:
            return
        lookup = oks_geo[oks_geo[key_col].fillna('').astype(str).str.strip().ne('')].sort_values(['registry', 'object_id']).drop_duplicates(key_col, keep='first')[[key_col, 'lat', 'lon']]
        if lookup.empty:
            return
        missing = out['lat'].isna() | out['lon'].isna()
        missing &= out[key_col].fillna('').astype(str).str.strip().ne('')
        if not missing.any():
            return
        joined = out.loc[missing, [key_col]].merge(lookup, on=key_col, how='left', suffixes=('', '_geo'))
        idx = out.index[missing]
        found = joined['lat'].notna() & joined['lon'].notna()
        if not found.any():
            return
        found_idx = idx[found.to_numpy()]
        out.loc[found_idx, 'lat'] = joined.loc[found, 'lat'].to_numpy()
        out.loc[found_idx, 'lon'] = joined.loc[found, 'lon'].to_numpy()
        out.loc[found_idx, 'coord_source'] = source_label
    fill_from_lookup('permit_key', 'geometry_permit')
    fill_from_lookup('address_key', 'geometry_address')
    fill_from_lookup('address_match_key', 'geometry_address_match')
    return out

def load_geocode_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=['registry', 'object_id', 'address_key', 'lat', 'lon', 'coord_source'])
    df = pd.read_csv(path, dtype={'registry': str, 'object_id': str})
    if 'address' in df.columns:
        df['address_key'] = df['address'].map(address_key)
    elif 'address_key' in df.columns:
        df['address_key'] = df['address_key'].map(address_key)
    if 'address' in df.columns:
        df['address_match_key'] = df['address'].map(address_match_key)
    elif 'address_match_key' in df.columns:
        df['address_match_key'] = df['address_match_key'].map(address_match_key)
    elif 'address_key' in df.columns:
        df['address_match_key'] = df['address_key'].map(address_match_key)
    for col in ('lat', 'lon'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df

def apply_geocode_cache(objects: pd.DataFrame, cache: pd.DataFrame | None) -> pd.DataFrame:
    if objects.empty:
        return objects
    if cache.empty:
        return objects
    out = objects.copy()
    cache_cols = [c for c in ['registry', 'object_id', 'lat', 'lon', 'coord_source', 'precision'] if c in cache.columns]
    cache_small = cache[cache_cols].dropna(subset=['lat', 'lon']).drop_duplicates(['registry', 'object_id'], keep='last')
    by_id = out.merge(cache_small, on=['registry', 'object_id'], how='left', suffixes=('', '_cache'))
    missing = by_id['lat'].isna() | by_id['lon'].isna()
    cache_has_coords = by_id.get('lat_cache', pd.Series(index=by_id.index)).notna() & by_id.get('lon_cache', pd.Series(index=by_id.index)).notna()
    source_priority = by_id.get('coord_source', pd.Series(index=by_id.index, dtype=object)).map(coord_source_priority)
    cache_priority = by_id.get('coord_source_cache', pd.Series(index=by_id.index, dtype=object)).map(coord_source_priority)
    use_cache_by_id = cache_has_coords & (missing | (cache_priority > source_priority))
    for col in ('lat', 'lon', 'coord_source', 'precision'):
        cache_col = f'{col}_cache'
        if cache_col in by_id.columns:
            by_id.loc[use_cache_by_id, col] = by_id.loc[use_cache_by_id, cache_col]
            by_id = by_id.drop(columns=[cache_col])
    still_missing = by_id['lat'].isna() | by_id['lon'].isna()
    if still_missing.any() and 'address_key' in cache.columns:
        addr_cols = [c for c in ['address_key', 'lat', 'lon', 'coord_source', 'precision'] if c in cache.columns]
        cache_addr = cache[cache['address_key'].fillna('').astype(str).str.strip().ne('')][addr_cols].dropna(subset=['lat', 'lon']).drop_duplicates('address_key', keep='last')
        addr_join = by_id.loc[still_missing, ['address_key']].merge(cache_addr, on='address_key', how='left', suffixes=('', '_cache'))
        idx = by_id.index[still_missing]
        for col in ('lat', 'lon', 'coord_source', 'precision'):
            if col in addr_join.columns:
                by_id.loc[idx, col] = addr_join[col].to_numpy()
    still_missing = by_id['lat'].isna() | by_id['lon'].isna()
    if still_missing.any() and 'address_match_key' in cache.columns and ('address_match_key' in by_id.columns):
        match_cols = [c for c in ['address_match_key', 'lat', 'lon', 'coord_source', 'precision'] if c in cache.columns]
        cache_match = cache[cache['address_match_key'].fillna('').astype(str).str.strip().ne('')][match_cols].dropna(subset=['lat', 'lon']).drop_duplicates('address_match_key', keep='last')
        match_join = by_id.loc[still_missing, ['address_match_key']].merge(cache_match, on='address_match_key', how='left', suffixes=('', '_cache'))
        idx = by_id.index[still_missing]
        for col in ('lat', 'lon', 'coord_source', 'precision'):
            if col in match_join.columns:
                by_id.loc[idx, col] = match_join[col].to_numpy()
        filled = by_id.index.isin(idx) & by_id['lat'].notna() & by_id['lon'].notna()
        by_id.loc[filled & by_id['coord_source'].fillna('').astype(str).eq(''), 'coord_source'] = 'address_match'
    return by_id

def apply_area_centroids(objects: pd.DataFrame) -> pd.DataFrame:
    """Fill remaining coordinates with transparent district/okrug centroids."""
    if objects.empty:
        return objects
    out = objects.copy()
    exact = out[out['lat'].notna() & out['lon'].notna() & ~out['coord_source'].fillna('').astype(str).str.contains('centroid', na=False)].copy()
    if exact.empty:
        return out

    def area_key(value: object) -> str:
        return clean_text(value).lower().replace('ё', 'е')

    def fill_by_area(area_col: str, source_label: str) -> None:
        nonlocal out
        if area_col not in out.columns or area_col not in exact.columns:
            return
        centroids = exact[exact[area_col].fillna('').astype(str).str.strip().ne('')].groupby(area_col, as_index=False).agg(lat=('lat', 'median'), lon=('lon', 'median'))
        if centroids.empty:
            return
        centroids = centroids.assign(__area_key=centroids[area_col].map(area_key))
        centroid_lookup = centroids[centroids['__area_key'].ne('')].drop_duplicates('__area_key', keep='first').set_index('__area_key')[['lat', 'lon']]
        missing = out['lat'].isna() | out['lon'].isna()
        missing &= out[area_col].fillna('').astype(str).str.strip().ne('')
        if not missing.any():
            return
        joined = out.loc[missing, [area_col]].merge(centroids, on=area_col, how='left')
        idx = out.index[missing]
        found = joined['lat'].notna() & joined['lon'].notna()
        if not found.any():
            return
        found_idx = idx[found.to_numpy()]
        out.loc[found_idx, 'lat'] = joined.loc[found, 'lat'].to_numpy()
        out.loc[found_idx, 'lon'] = joined.loc[found, 'lon'].to_numpy()
        out.loc[found_idx, 'coord_source'] = source_label
        out.loc[found_idx, 'precision'] = 'approximate'
        still_missing = out['lat'].isna() | out['lon'].isna()
        still_missing &= out[area_col].fillna('').astype(str).str.strip().ne('')
        for idx, value in out.loc[still_missing, area_col].items():
            parts = [area_key(part) for part in re.split('[,;/]', str(value))]
            hits = [centroid_lookup.loc[part] for part in parts if part in centroid_lookup.index]
            if not hits:
                continue
            coords = pd.DataFrame(hits)
            out.loc[idx, 'lat'] = float(coords['lat'].median())
            out.loc[idx, 'lon'] = float(coords['lon'].median())
            out.loc[idx, 'coord_source'] = f'{source_label}_multi'
            out.loc[idx, 'precision'] = 'approximate'
        if area_col == 'district' and 'address' in out.columns:
            still_missing = out['lat'].isna() | out['lon'].isna()
            for idx, address in out.loc[still_missing, 'address'].items():
                address_text = area_key(address)
                if not address_text:
                    continue
                matches = [centroid_lookup.loc[key] for key in centroid_lookup.index if len(key) >= 5 and re.search(f'\\b{re.escape(key)}\\b', address_text)]
                if not matches:
                    continue
                coords = pd.DataFrame(matches)
                out.loc[idx, 'lat'] = float(coords['lat'].median())
                out.loc[idx, 'lon'] = float(coords['lon'].median())
                out.loc[idx, 'coord_source'] = 'district_centroid_address'
                out.loc[idx, 'precision'] = 'approximate'
    fill_by_area('district', 'district_centroid')
    fill_by_area('okrug', 'okrug_centroid')
    return out
