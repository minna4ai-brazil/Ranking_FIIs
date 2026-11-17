#!/usr/bin/env python3
"""Monitor de FIIs: baixa dados públicos e gera os arquivos de dados das páginas.

Fontes (todas públicas, sem cadastro):
  - CVM, dados abertos: informes mensais e trimestrais dos FIIs
    https://dados.cvm.gov.br/dados/FII/DOC/
  - mFinance: cotações, volume e dividendos por fundo
    https://mfinance.com.br/api/v1/fiis/<TICKER>
  - Municípios do Brasil (coordenadas): github.com/kelvins/municipios-brasileiros

Uso:  python3 build_data.py --out ../site/data [--cache ../.cache]
Só usa a biblioteca padrão do Python 3.9+.
"""
import argparse, concurrent.futures as cf, csv, datetime as dt, io, json, math, os, re, sys, time, unicodedata, urllib.request, urllib.error, zipfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
UA = 'Mozilla/5.0 (fii-monitor; dados publicos CVM)'
CVM = 'https://dados.cvm.gov.br/dados/FII/DOC/%s/DADOS/%s'
MF = 'https://mfinance.com.br/api/v1/fiis/%s'
MUN = 'https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv'
EST = 'https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/estados.csv'

# ---------------------------------------------------------------- download
def get(url, cache=None, max_age_h=20, tries=3):
    if cache:
        fn = os.path.join(cache, re.sub(r'[^A-Za-z0-9._-]+', '_', url)[-180:])
        if os.path.exists(fn) and time.time() - os.path.getmtime(fn) < max_age_h * 3600:
            return open(fn, 'rb').read()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            if cache:
                os.makedirs(cache, exist_ok=True); open(fn, 'wb').write(data)
            return data
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError('HTTP %d em %s' % (e.code, url))
            last = e; time.sleep(2 + 3 * i)
        except Exception as e:  # noqa
            last = e; time.sleep(2 + 3 * i)
    raise RuntimeError('falha ao baixar %s: %s' % (url, last))

def cvm_zip(kind, year, cache):
    name = 'inf_%s_fii_%d.zip' % (kind.lower().replace('inf_', ''), year)
    try:
        raw = get(CVM % (kind, name), cache)
    except RuntimeError:
        return {}
    z = zipfile.ZipFile(io.BytesIO(raw))
    out = {}
    for n in z.namelist():
        key = re.sub(r'^inf_(mensal|trimestral)_fii_|_\d{4}\.csv$', '', n)
        out[key] = list(csv.DictReader(io.TextIOWrapper(z.open(n), encoding='latin1'), delimiter=';'))
    return out

def merge_years(kind, years, cache):
    acc = defaultdict(list)
    for y in years:
        for k, v in cvm_zip(kind, y, cache).items():
            acc[k].extend(v)
    return acc

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def latest(rows, key='Data_Referencia'):
    """Última versão do último mês por CNPJ."""
    best = {}
    for r in rows:
        c = r['CNPJ_Fundo_Classe']; k = (r[key], int(r.get('Versao') or 0))
        if c not in best or k > best[c][0]:
            best[c] = (k, r)
    return {c: v[1] for c, v in best.items()}

def by_period(rows, key='Data_Referencia'):
    """{cnpj: {data: linha (última versão)}}"""
    out = defaultdict(dict)
    for r in rows:
        c, d = r['CNPJ_Fundo_Classe'], r[key]
        if d not in out[c] or int(r.get('Versao') or 0) > int(out[c][d].get('Versao') or 0):
            out[c][d] = r
    return out

def decl_tri(rh, d):
    """Rendimentos declarados NO trimestre. A CVM acumula o semestre em junho e dezembro."""
    v = num(rh[d]['Rendimentos_Declarados']) or 0
    if d[5:7] in ('06', '12'):
        prev = d[:4] + ('-03-31' if d[5:7] == '06' else '-09-30')
        if prev in rh:
            v -= num(rh[prev]['Rendimentos_Declarados']) or 0
    return max(v, 0)

def pct(x):
    """Percentual informado como fração (0,12). Corrige quem informou em % e descarta absurdos."""
    v = num(x)
    if v is None: return None
    if v > 1: v = v / 100 if v <= 100 else None
    return v if v is None or v >= 0 else None

def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()

# ---------------------------------------------------------------- geografia
UFS = 'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split()
UFNOME = {}
BAIRROS = {  # posições aproximadas de regiões conhecidas (lat, lon)
 ('SP', 'faria lima'): (-23.5800, -46.6870), ('SP', 'vila olimpia'): (-23.5955, -46.6860), ('SP', 'itaim'): (-23.5845, -46.6755),
 ('SP', 'juscelino kubitschek'): (-23.5880, -46.6800), ('SP', 'paulista'): (-23.5614, -46.6559), ('SP', 'chucri zaidan'): (-23.6140, -46.6953),
 ('SP', 'berrini'): (-23.6050, -46.6930), ('SP', 'pinheiros'): (-23.5660, -46.6870), ('SP', 'jardins'): (-23.5700, -46.6600),
 ('SP', 'cidade jardim'): (-23.5830, -46.6880), ('SP', 'chacara santo antonio'): (-23.6330, -46.7010), ('SP', 'santo amaro'): (-23.6480, -46.7200),
 ('SP', 'tatuape'): (-23.5400, -46.5760), ('SP', 'higienopolis'): (-23.5420, -46.6580), ('SP', 'morumbi'): (-23.6230, -46.7000),
 ('SP', 'vila leopoldina'): (-23.5280, -46.7300), ('SP', 'barra funda'): (-23.5250, -46.6650), ('SP', 'bela vista'): (-23.5590, -46.6440),
 ('SP', 'moema'): (-23.6000, -46.6650), ('SP', 'brooklin'): (-23.6150, -46.6880), ('SP', 'vila mariana'): (-23.5890, -46.6340),
 ('SP', 'consolacao'): (-23.5530, -46.6570), ('SP', 'centro'): (-23.5470, -46.6360), ('SP', 'lapa'): (-23.5220, -46.7050),
 ('SP', 'jaguare'): (-23.5450, -46.7500), ('SP', 'butanta'): (-23.5710, -46.7090), ('SP', 'mooca'): (-23.5580, -46.5990),
 ('SP', 'santana'): (-23.4970, -46.6250), ('SP', 'tucuruvi'): (-23.4800, -46.6030), ('SP', 'saude'): (-23.6110, -46.6260),
 ('SP', 'perdizes'): (-23.5370, -46.6780), ('SP', 'vila guilherme'): (-23.5100, -46.6050), ('SP', 'jabaquara'): (-23.6460, -46.6410),
 ('RJ', 'barra da tijuca'): (-23.0000, -43.3650), ('RJ', 'botafogo'): (-22.9510, -43.1840), ('RJ', 'centro'): (-22.9035, -43.1790),
 ('RJ', 'humaita'): (-22.9570, -43.1990), ('RJ', 'tijuca'): (-22.9250, -43.2330), ('RJ', 'jacarepagua'): (-22.9330, -43.3600),
 ('RJ', 'campo grande'): (-22.9020, -43.5600), ('RJ', 'bangu'): (-22.8790, -43.4650), ('RJ', 'santa cruz'): (-22.9200, -43.6850),
 ('RJ', 'ilha do governador'): (-22.8050, -43.2100), ('RJ', 'leblon'): (-22.9840, -43.2230), ('RJ', 'copacabana'): (-22.9700, -43.1870),
 ('RJ', 'cidade nova'): (-22.9110, -43.2050), ('RJ', 'sao cristovao'): (-22.8990, -43.2220), ('RJ', 'recreio'): (-23.0150, -43.4600),
}
CAPITAIS = {'SP': 'sao paulo', 'RJ': 'rio de janeiro'}
STREET = re.compile(r'(rua|r|av|avenida|alameda|al|rodovia|rod|estrada|estr|est|travessa|tv|praca|largo|via|marginal|br|sp|km|vila|vl|jardim|jd|parque|pq|bairro|chacara|residencial|conjunto|edificio|ed|shopping|condominio|fazenda|faz|loteamento|setor|quadra|centro|dos|das|do|da|de|padre|dom|presidente|marechal|general|doutor|dr|engenheiro|eng|prof|professor|governador|senador|deputado|coronel|santa|sao)$')

class Geo:
    def __init__(self, cache):
        est = list(csv.DictReader(io.StringIO(get(EST, cache, 24 * 30).decode('utf-8-sig'))))
        code2uf = {r['codigo_uf']: r['uf'] for r in est}
        for r in est:
            UFNOME[norm(r['nome'])] = r['uf']
        self.mun = defaultdict(list)  # nome normalizado -> [(uf, lat, lon, nome)]
        for r in csv.DictReader(io.StringIO(get(MUN, cache, 24 * 30).decode('utf-8-sig'))):
            self.mun[norm(r['nome'])].append((code2uf[r['codigo_uf']], float(r['latitude']), float(r['longitude']), r['nome']))
        self.names = sorted(self.mun, key=len, reverse=True)
        self.rx = re.compile(r'\b(' + '|'.join(re.escape(n) for n in self.names) + r')\b')

    def _cands(self, t, ufs, bonus):
        out = []
        for m in self.rx.finditer(t):
            before = t[:m.start()].split()[-1:] if m.start() else []
            if before and STREET.match(before[0]):
                continue  # "Avenida Paulista", "Vila Madalena", "Rua Bahia"
            for uf, lat, lon, nome in self.mun[m.group(1)]:
                score = bonus + m.end() / max(len(t), 1) + 0.01 * len(m.group(1))
                if ufs:
                    score += 2 if uf in ufs else -1.5
                out.append((score, uf, lat, lon, nome))
        return out

    def locate(self, endereco, nome=''):
        te, tn = norm(endereco), norm(nome)
        ufs = set()
        for raw in (endereco or '', nome or ''):
            # sigla de UF em maiúsculas depois de "-", "/" ou "," ou no fim do texto
            ufs |= set(re.findall(r'(?:[-/,]\s*|\s)(%s)\b(?=\s*(?:[-,.;)]|$|\d))' % '|'.join(UFS), raw))
            t = norm(raw)
            for nm, uf in UFNOME.items():
                if len(nm) > 5 and re.search(r'\b%s\b' % re.escape(nm), t) and nm not in ('sao paulo', 'rio de janeiro'): ufs.add(uf)
        cands = []
        # 1º: uma parte do endereço (entre vírgulas, traços ou barras) que seja exatamente um município
        partes = {}
        for fonte, raw in (('end', endereco or ''), ('nome', nome or '')):
            comps = [re.sub(r'^.*\b(municipio de|cidade de|zona rural de|distrito de)\s+', '', norm(x)) for x in re.split(r'[,;/()\-–]| {2,}', raw)]
            partes[fonte] = [(i, comp, len(comps)) for i, comp in enumerate(comps) if comp in self.mun]
        # o nome do imóvel costuma trazer "Cidade - UF" ou "Cidade/UF": vale como pista de estado
        for i, comp, n in partes['nome']:
            if i > 0 or len(partes['nome']) > 1:
                ufs |= {x[0] for x in self.mun[comp]} if len(self.mun[comp]) == 1 else set()
        for fonte, peso in (('end', 3.0), ('nome', 2.0)):
            achados = partes[fonte]
            for i, comp, n in achados:
                outras = [c for _, c, _ in achados if c != comp]
                for uf, lat, lon, nm in self.mun[comp]:
                    if fonte == 'nome' and i == 0 and not ufs: continue
                    sc = peso + i / max(n, 1) + (2 if uf in ufs else (-2 if ufs else 0))
                    if comp in ('sao paulo', 'rio de janeiro') and outras: sc -= 0.8  # "Franca - São Paulo/SP": estado, não cidade
                    cands.append((sc, uf, lat, lon, nm))
        cands = [c for c in cands if c[0] > 1.5]
        if not cands and ufs:
            cands = [c for c in self._cands(te, ufs, 1.0) if c[0] > 0.3 and c[1] in ufs]
        if not cands:
            # último recurso: nome de município único no Brasil escrito no nome do imóvel ("Pernambucanas Botucatu")
            for m in self.rx.finditer(tn):
                nm = m.group(1)
                if len(self.mun[nm]) == 1 and len(nm) >= 5:
                    uf, lat, lon, nome_c = self.mun[nm][0]
                    cands.append((1 + len(nm) / 100, uf, lat, lon, nome_c))
        uf_only = None
        if not cands and len(ufs) == 1:
            uf_only = next(iter(ufs))
        elif cands and ufs and cands and max(cands)[1] not in ufs and len(ufs) == 1:
            uf_only = next(iter(ufs))
        if uf_only in CAPITAIS:
            nome_cap = {'SP': 'sao paulo', 'RJ': 'rio de janeiro'}[uf_only]
            uf, lat, lon, nm = [x for x in self.mun[nome_cap] if x[0] == uf_only][0]
            cands = [(9, uf, lat, lon, nm)]
        if not cands:
            return None
        cands.sort(reverse=True)
        _, uf, lat, lon, nome_c = cands[0]
        prec = 'cidade'
        if norm(nome_c) == CAPITAIS.get(uf):
            for (u, b), (la, lo) in BAIRROS.items():
                if u == uf and (re.search(r'\b%s\b' % re.escape(b), te) or re.search(r'\b%s\b' % re.escape(b), tn)):
                    lat, lon, prec = la, lo, 'bairro'
                    break
        return dict(cidade=nome_c, uf=uf, lat=lat, lon=lon, prec=prec)

# ---------------------------------------------------------------- referências de mercado
REFS = json.load(open(os.path.join(HERE, 'refs.json'), encoding='utf-8'))

def tipo_imovel(nome, endereco, seg_fundo):
    t = norm(nome + ' ' + endereco)
    if re.search(r'\b(shopping|mall|outlet|plaza shopping)\b', t): return 'Shopping'
    if re.search(r'\b(cd|galpao|galpoes|log|logistic\w*|condominio logistico|armazem|industrial|centro de distribuicao|hub|dc)\b', t): return 'Galpão'
    if re.search(r'\b(hospital|clinica|laborator\w*)\b', t): return 'Saúde'
    if re.search(r'\b(escola|faculdade|universidade|campus|colegio|educacional)\b', t): return 'Educação'
    if re.search(r'\b(hotel|resort|pousada)\b', t): return 'Hotel'
    if re.search(r'\b(assai|atacadao|carrefour|sam s|supermercado|hipermercado|loja|varejo|mateus|leroy)\b', t): return 'Loja'
    if re.search(r'\b(agencia|banco|caixa|itau|bradesco|santander|banrisul)\b', t): return 'Agência bancária'
    if re.search(r'\b(edificio|ed|torre|tower|corporate|office|offices|escritorio\w*|lajes?|conjunto|business center|trade center)\b', t): return 'Escritório'
    s = norm(seg_fundo)
    if 'logist' in s or 'industr' in s: return 'Galpão'
    if 'shopping' in s: return 'Shopping'
    if 'lajes' in s or 'escrit' in s: return 'Escritório'
    if 'hosp' in s: return 'Saúde'
    if 'hotel' in s: return 'Hotel'
    return 'Outro'

def ref_aluguel(tipo, geo, endereco):
    if not geo: return None
    t = norm(endereco)
    if tipo == 'Galpão':
        r = REFS['galpao_sp'] if geo['uf'] == 'SP' else REFS['galpao_br']
        return r
    if tipo == 'Escritório' and geo['uf'] == 'SP' and norm(geo['cidade']) == 'sao paulo':
        for key, words in (('esc_faria_lima', ('faria lima',)), ('esc_jk', ('juscelino', ' jk ', 'itaim', 'vila olimpia')),
                           ('esc_pinheiros', ('pinheiros',)), ('esc_chucri', ('chucri', 'zaidan'))):
            if any(w.strip() in t for w in words):
                return REFS[key]
        return REFS['esc_sp']
    return None

# ---------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'site', 'data'))
    ap.add_argument('--cache', default=os.path.join(HERE, '..', '.cache'))
    ap.add_argument('--min-pl', type=float, default=300e6)
    ap.add_argument('--min-liq', type=float, default=500e3)
    ap.add_argument('--hoje', default=None)
    a = ap.parse_args()
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.date.today()
    os.makedirs(a.out, exist_ok=True)
    log = lambda *x: print(*x, file=sys.stderr, flush=True)
    anos = [hoje.year - 1, hoje.year]

    log('CVM: informes mensais', anos)
    men = merge_years('INF_MENSAL', anos, a.cache)
    log('CVM: informes trimestrais', anos)
    tri = merge_years('INF_TRIMESTRAL', anos, a.cache)

    g_last = latest(men['geral']); c_hist = by_period(men['complemento']); a_last = latest(men['ativo_passivo'])
    c_last = latest(men['complemento'])
    res_hist = by_period(tri['resultado_contabil_financeiro'])
    im_last_date = {}
    for r in tri['imovel']:
        c = r['CNPJ_Fundo_Classe']; im_last_date[c] = max(im_last_date.get(c, ''), r['Data_Referencia'])
    imoveis = defaultdict(list); seen = set()
    for r in sorted(tri['imovel'], key=lambda r: -int(r.get('Versao') or 0)):
        c = r['CNPJ_Fundo_Classe']
        if r['Data_Referencia'] != im_last_date[c]: continue
        k = (c, r['Nome_Imovel'], r['Endereco'], r['Area'])
        if k in seen: continue
        seen.add(k); imoveis[c].append(r)
    ativos_tri = defaultdict(list)
    at_last = {}
    for r in tri['ativo']:
        c = r['CNPJ_Fundo_Classe']; at_last[c] = max(at_last.get(c, ''), r['Data_Referencia'])
    for r in tri['ativo']:
        if r['Data_Referencia'] == at_last[r['CNPJ_Fundo_Classe']]:
            ativos_tri[r['CNPJ_Fundo_Classe']].append(r)

    # universo: listados em bolsa, com ISIN e PL mínimo
    cands = []
    for c, g in g_last.items():
        isin = (g.get('Codigo_ISIN') or '').strip().upper()
        if not re.match(r'^BR[A-Z]{4}CTF', isin): continue
        if re.search(r'agro|agronegoc|energia|infra', norm(g['Nome_Fundo_Classe'])): continue  # Fiagro, energia e infraestrutura
        if (g.get('Mercado_Negociacao_Bolsa') or '')[:1].upper() != 'S': continue
        comp = c_last.get(c)
        pl = num(comp and comp['Patrimonio_Liquido']) or 0
        if pl < a.min_pl: continue
        cands.append((c, isin[2:6] + '11', g, comp))
    log('candidatos (PL >= %.0f mi): %d' % (a.min_pl / 1e6, len(cands)))

    ini12 = hoje - dt.timedelta(days=365)
    def pre(tk):
        for u in (MF % tk, MF % ('dividends/' + tk)):
            try: get(u, a.cache, 18)
            except Exception: return
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(pre, [x[1] for x in cands]))
    log('cotações baixadas')
    manuais = {}
    mp = os.path.join(HERE, 'precos_complementares.json')
    if os.path.exists(mp):
        manuais = {k: v for k, v in json.load(open(mp, encoding='utf-8')).items() if not k.startswith('_')}
    faltando = []
    fundos = []
    for i, (c, tk, g, comp) in enumerate(sorted(cands, key=lambda x: x[1])):
        cot = int(num(comp['Total_Numero_Cotistas']) or 0)
        q = cotacao(tk, a.cache)
        fonte_preco = 'mFinance'
        if not q:
            q = investidor10(tk, a.cache); fonte_preco = 'Investidor10'
        if not q:
            q = yahoo(tk); fonte_preco = 'Yahoo Finance'
        if not q and tk in manuais:
            m = manuais[tk]
            if (hoje - dt.date.fromisoformat(m['data'])).days <= 45:
                q = dict(lastPrice=m['preco'], volumeAvg=(m.get('liq') or 0) / m['preco'], lastYearHigh=m.get('max52'), lastYearLow=m.get('min52'), segment=m.get('segmento'))
                fonte_preco = 'complementar (%s, %s)' % (m.get('fonte', ''), m['data'])
        if not q:
            if cot >= 20000:
                faltando.append(dict(t=tk, cnpj=c, nome=g['Nome_Fundo_Classe'], cotistas=cot))
            continue
        try:
            dv = json.loads(get(MF % ('dividends/' + tk), a.cache, 18)).get('dividends') or []
        except Exception:
            dv = []
        preco = q['lastPrice']
        liq = (q.get('volumeAvg') or 0) * preco
        if liq < a.min_liq and not (liq == 0 and cot >= 20000): continue
        pagos = [dt.date.fromisoformat(d['payDate'][:10]) for d in dv if d.get('payDate')]
        ult_pg = max([x for x in pagos if x <= hoje], default=None)
        # o mFinance publica com atraso: a janela de 12 meses termina no último pagamento disponível
        fim = ult_pg if ult_pg and (hoje - ult_pg).days <= 120 else hoje
        divs12 = [d for d in dv if d.get('payDate') and fim - dt.timedelta(days=364) < dt.date.fromisoformat(d['payDate'][:10]) <= fim]
        d12 = sum(d['value'] for d in divs12)
        fonte_div = 'mFinance (12 meses até %s)' % fim.strftime('%d/%m/%Y')
        if not divs12:
            rh0 = res_hist.get(c, {}); q4 = sorted(rh0)[-4:]
            cotas = num(comp['Cotas_Emitidas']) or 0
            if len(q4) == 4 and cotas:
                d12 = sum(decl_tri(rh0, d) for d in q4) / cotas
                fonte_div = 'CVM (rendimentos declarados em 4 trimestres)'
        ult = max(dv, key=lambda d: d.get('payDate') or '') if dv else None
        ap_ = a_last.get(c, {})
        vp = num(comp['Valor_Patrimonial_Cotas']); pl = num(comp['Patrimonio_Liquido']); at = num(comp['Valor_Ativo']) or 0
        f = lambda k: num(ap_.get(k)) or 0.0
        imov = f('Direitos_Bens_Imoveis'); soc = f('Acoes_Sociedades_Atividades_FII') + f('Cotas_Sociedades_Atividades_FII')
        cri = f('CRI') + f('CRI_CRA'); fii = f('FII'); liqd = f('Total_Necessidades_Liquidez')
        passivo = f('Total_Passivo'); rend_pagar = f('Rendimentos_Distribuir')
        divida = f('Obrigacoes_Aquisicao_Imoveis') + f('Obrigacoes_Securitizacao_Recebiveis')
        base = max(at, 1)
        if (imov + soc) / base >= 0.5: tipo = 'Tijolo'
        elif cri / base >= 0.5: tipo = 'Papel'
        elif fii / base >= 0.5: tipo = 'Fundo de fundos'
        else: tipo = 'Híbrido'
        if tipo == 'Fundo de fundos':  # estruturas master/feeder: o fundo investe em um FII que detém os ativos
            segn, nmn = norm(q.get('segment') or ''), norm(g['Nome_Fundo_Classe'])
            if 'fundo de fundos' in segn or re.search(r'\b(fof\w*|fundo de fundos|alpha|hedge|rendimentos|total return|multi\w*|rinv)\b', nmn):
                pass
            elif re.search(r'logist|industr|shopping|lajes|escrit|varejo|hibrid|renda urbana|hosp|educ', segn):
                tipo = 'Tijolo via outro FII'
            elif 'pape' in segn or 'receb' in segn or 'cri' in nmn:
                tipo = 'Papel via outro FII'
        # resultado e rendimentos: últimos 4 trimestres
        rh = res_hist.get(c, {})
        qs = sorted(rh)[-4:]
        res12 = sum(num(rh[d]['Resultado_Trimestral_Liquido_Financeiro']) or 0 for d in qs)
        decl12 = sum(decl_tri(rh, d) for d in qs)
        alug_tri = num(rh[qs[-1]]['Receita_Aluguel_Investimento_Financeiro']) if qs else None
        alug12 = sum(num(rh[d]['Receita_Aluguel_Investimento_Financeiro']) or 0 for d in qs) if len(qs) == 4 else None
        cob = (res12 / decl12) if decl12 > 0 else None
        mcap = preco * (num(comp['Cotas_Emitidas']) or 0)
        # meses de DY mensal (patrimonial) para checar consistência
        ch = c_hist.get(c, {})
        taxa_adm = num(comp.get('Percentual_Despesas_Taxa_Administracao'))
        rec = dict(
            t=tk, cnpj=c, nome=g['Nome_Fundo_Classe'].title(), adm=g['Nome_Administrador'].title(), seg=q.get('segment') or g.get('Segmento_Atuacao'),
            tipo=tipo, preco=round(preco, 2), vp=round(vp, 2) if vp else None, pvp=round(preco / vp, 3) if vp else None,
            dy12=round(d12 / preco * 100, 2) if preco else None, div12=round(d12, 4), ult_div=ult and ult['value'], ult_div_data=ult and ult['payDate'][:10],
            dy_ult_anual=round(ult['value'] * 12 / preco * 100, 2) if ult else None,
            liq=round(liq), pl=round(pl), mcap=round(mcap), cotistas=int(num(comp['Total_Numero_Cotistas']) or 0),
            max52=q.get('lastYearHigh'), min52=q.get('lastYearLow'),
            dist_max52=round((preco / q['lastYearHigh'] - 1) * 100, 1) if q.get('lastYearHigh') else None,
            cob=round(cob, 2) if cob else None, alav=round((passivo - rend_pagar) / base * 100, 1), divida_pl=round(divida / pl * 100, 1) if pl else None,
            taxa_adm=round(taxa_adm * 100, 3) if taxa_adm else None,
            pct_imoveis=round((imov + soc) / base * 100, 1), pct_cri=round(cri / base * 100, 1), pct_fii=round(fii / base * 100, 1), pct_caixa=round(liqd / base * 100, 1),
            data_cvm=comp['Data_Referencia'], data_tri=qs[-1] if qs else None, fonte_preco=fonte_preco, fonte_div=fonte_div,
        )
        # carteira imobiliária
        ims = imoveis.get(c, []) if tipo in ('Tijolo', 'Híbrido') else []
        if ims:
            ren = [r for r in ims if r['Classe'].startswith('Imóveis para renda')]
            area = sum(num(r['Area']) or 0 for r in ren)
            if area > 0:
                vac = sum((num(r['Area']) or 0) * (pct(r['Percentual_Vacancia']) or 0) for r in ren) / area
                pr = [pct(r['Percentual_Receitas_FII']) or 0 for r in ren]
                spr = sum(pr) or None
                inad = (sum(p * (pct(r['Percentual_Inadimplencia']) or 0) for p, r in zip(pr, ren)) / spr) if spr else None
                rec.update(n_imoveis=len(ren), area=round(area), vac_fis=round(vac * 100, 1), inad=round(inad * 100, 1) if inad is not None else None,
                           conc_max=round(max(pr) / spr * 100, 1) if spr else None)
                if alug12 and area * (1 - vac) > 0 and soc / max(imov + soc, 1) < 0.2:
                    am = alug12 / 12 / (area * (1 - vac))
                    if 3 <= am <= 600: rec['alug_m2'] = round(am, 2)
        if tipo in ('Tijolo', 'Híbrido') and alug12 and imov > 0 and soc / max(imov + soc, 1) < 0.2 and 2 <= alug12 / imov * 100 <= 20:
            rec['cap_contabil'] = round(alug12 / imov * 100, 2)
            ev = mcap + passivo - rend_pagar - liqd - cri - fii - f('Titulos_Publicos') - f('Titulos_Privados') - f('Fundos_Renda_Fixa') * 0
            if ev > 0: rec['cap_mercado'] = round(alug12 / ev * 100, 2)
        if tipo in ('Papel', 'Híbrido'):
            cris = [r for r in ativos_tri.get(c, []) if r['Tipo'] == 'CRI/CRA']
            tot = sum(num(r['Valor']) or 0 for r in cris)
            if tot > 0:
                rec['n_cri'] = len(cris); rec['conc_cri'] = round(max(num(r['Valor']) or 0 for r in cris) / tot * 100, 1)
        fundos.append(rec)
        if i % 25 == 0: log('cotações: %d/%d' % (i, len(cands)))

    pontuar(fundos)
    meta = dict(gerado=hoje.isoformat(), gerado_em=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                cvm_mensal=max(f['data_cvm'] for f in fundos), cvm_trimestral=max(f['data_tri'] or '' for f in fundos), refs=REFS,
                n=len(fundos), criterio=dict(min_pl=a.min_pl, min_liq=a.min_liq))
    meta['faltando_preco'] = faltando
    json.dump(dict(meta=meta, fundos=fundos), open(os.path.join(a.out, 'fundos.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    json.dump(faltando, open(os.path.join(a.out, 'faltando_preco.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if faltando: log('sem cotação automática (>= 20 mil cotistas): ' + ', '.join(x['t'] for x in faltando))
    top20 = sorted([f for f in fundos if f.get('top')], key=lambda f: f['top'])
    json.dump(dict(gerado=meta['gerado'], cvm_mensal=meta['cvm_mensal'], cvm_trimestral=meta['cvm_trimestral'], n=len(fundos),
                   top20=[dict(t=f['t'], nota=f['score'], tipo=f['tipo'], dy=f['dy12'], pvp=f['pvp']) for f in top20], faltando_preco=[x['t'] for x in faltando]),
              open(os.path.join(a.out, 'resumo.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    log('fundos.json: %d fundos' % len(fundos))

    # ---------------- imóveis para o mapa
    geo = Geo(a.cache)
    mapa = json.load(open(os.path.join(HERE, 'mapa_fundos.json'), encoding='utf-8'))
    top_tij = [f['t'] for f in sorted(fundos, key=lambda f: -f['score'])[:30] if f['tipo'] == 'Tijolo']
    alvo = list(dict.fromkeys(mapa + top_tij))
    fx = {f['t']: f for f in fundos}
    props = []; sem_local = 0
    for tk in alvo:
        f = fx.get(tk)
        if not f: continue
        c = f['cnpj']
        ren = [r for r in imoveis.get(c, []) if r['Classe'].startswith('Imóveis para renda')]
        rh = res_hist.get(c, {}); qs = sorted(rh)[-4:]
        alug12 = sum(num(rh[d]['Receita_Aluguel_Investimento_Financeiro']) or 0 for d in qs) if len(qs) == 4 else None
        spr = sum(pct(r['Percentual_Receitas_FII']) or 0 for r in ren) or None
        for r in ren:
            area = num(r['Area']) or 0; vac = pct(r['Percentual_Vacancia']) or 0; inad = pct(r['Percentual_Inadimplencia']) or 0
            pr = pct(r['Percentual_Receitas_FII']) or 0
            gl = geo.locate(r['Endereco'], r['Nome_Imovel'])
            if not gl: sem_local += 1; continue
            tp = tipo_imovel(r['Nome_Imovel'], r['Endereco'], f['seg'] or '')
            alug = None
            if tp != 'Shopping' and alug12 and spr and area * (1 - vac) > 0 and f.get('alug_m2') is not None:
                alug = alug12 * (pr / spr) / 12 / (area * (1 - vac))
                if alug > 1500 or alug < 1: alug = None
            ref = ref_aluguel(tp, gl, r['Endereco'] + ' ' + r['Nome_Imovel'])
            p = dict(f=tk, n=r['Nome_Imovel'].strip()[:90], end=r['Endereco'].strip()[:140], c=gl['cidade'], uf=gl['uf'], lat=gl['lat'], lon=gl['lon'], prec=gl['prec'],
                     tipo=tp, area=round(area), vac=round(vac * 100, 1), inad=round(inad * 100, 1), prec_rec=round(pr / spr * 100, 2) if spr else None,
                     alug=round(alug, 2) if alug else None, ref=ref and ref['valor'], ref_n=ref and ref['nome'])
            p['v'], p['o'] = leitura_imovel(p, f)
            props.append(p)
    espalhar(props)
    json.dump(dict(meta=meta, fundos=[fx[t] for t in alvo if t in fx], props=props), open(os.path.join(a.out, 'imoveis.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    log('imoveis.json: %d imóveis de %d fundos (%d sem localização)' % (len(props), len(alvo), sem_local))

def cotacao(tk, cache):
    """mFinance; descarta cotação fora da faixa de 52 semanas."""
    try:
        q = json.loads(get(MF % tk, cache, 18))
    except Exception:
        return None
    lo, hi = q.get('lastYearLow') or 0, q.get('lastYearHigh') or 0
    ok = lambda p: p and p > 0 and (not lo or not hi or 0.8 * lo <= p <= 1.2 * hi)
    for k in ('lastPrice', 'closingPrice'):
        if ok(q.get(k)):
            q['lastPrice'] = q[k]; return q
    return None

def brnum(x):
    return float(x.replace('.', '').replace(',', '.'))

def investidor10(tk, cache):
    """Página pública do fundo: cotação, liquidez diária e variação em 12 meses."""
    try:
        raw = get('https://investidor10.com.br/fiis/%s/' % tk.lower(), cache, 18).decode('utf-8', 'ignore')
    except Exception:
        return None
    import html as _h
    t = _h.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', raw)))
    m = re.search(r'%s Cota\w+ R\$ ([\d.]+,\d+)' % tk, t)
    if not m: return None
    q = dict(lastPrice=brnum(m.group(1)), volumeAvg=0, lastYearHigh=None, lastYearLow=None, segment=None)
    m = re.search(r'Liquidez Di\w+ R\$ ([\d.,]+) ?(M|K|Mil|Mi)?', t)
    if m:
        v = brnum(m.group(1)) * {'M': 1e6, 'Mi': 1e6, 'K': 1e3, 'Mil': 1e3}.get(m.group(2) or '', 1)
        q['volumeAvg'] = v / q['lastPrice']
    return q

def yahoo(tk):
    """Alternativa quando o mFinance não cobre o fundo (costuma funcionar no GitHub Actions)."""
    try:
        raw = urllib.request.urlopen(urllib.request.Request(
            'https://query1.finance.yahoo.com/v8/finance/chart/%s.SA?range=1y&interval=1d' % tk, headers={'User-Agent': UA}), timeout=20).read()
        r = json.loads(raw)['chart']['result'][0]
        cl = [x for x in r['indicators']['quote'][0]['close'] if x]
        vol = [v for v in r['indicators']['quote'][0]['volume'][-60:] if v]
        if not cl: return None
        return dict(lastPrice=cl[-1], volumeAvg=sum(vol) / len(vol) if vol else 0, lastYearHigh=max(cl), lastYearLow=min(cl), segment=None)
    except Exception:
        return None

def leitura_imovel(p, f):
    motivos = []; v = 'ok'
    def pior(a, b): return max(a, b, key=['ok', 'ap', 'du'].index)
    if p['vac'] >= 30: v = 'du'; motivos.append('Vacância de %s%%.' % fmt(p['vac']))
    elif p['vac'] >= 15: v = pior(v, 'ap'); motivos.append('Vacância de %s%%.' % fmt(p['vac']))
    if p['inad'] >= 10: v = 'du'; motivos.append('Inadimplência de %s%%.' % fmt(p['inad']))
    elif p['inad'] >= 3: v = pior(v, 'ap'); motivos.append('Inadimplência de %s%%.' % fmt(p['inad']))
    if p['alug'] and p['ref']:
        r = p['alug'] / p['ref']
        if r > 2.5: motivos.append('Aluguel calculado de R$ %s/m² é %s vezes a referência de mercado: provável diferença de área ou receita no informe, não usado na leitura.' % (fmt(p['alug'], 2), fmt(r)))
        elif r > 1.6: v = pior(v, 'ap'); motivos.append('Aluguel de R$ %s/m², %d%% acima da referência de mercado (R$ %s): risco na renovação.' % (fmt(p['alug'], 2), round((r - 1) * 100), fmt(p['ref'], 2)))
        elif r < 0.75: motivos.append('Aluguel de R$ %s/m², %d%% abaixo da referência de mercado (R$ %s): espaço para reajuste.' % (fmt(p['alug'], 2), round((1 - r) * 100), fmt(p['ref'], 2)))
        else: motivos.append('Aluguel de R$ %s/m², próximo da referência de mercado (R$ %s).' % (fmt(p['alug'], 2), fmt(p['ref'], 2)))
    cap = f.get('cap_contabil')
    if cap is not None:
        if cap < 6.5: v = pior(v, 'ap'); motivos.append('O fundo rende %s%% ao ano sobre o valor contábil dos imóveis: laudo exigente.' % fmt(cap))
    if not motivos: motivos.append('Ocupado e adimplente.')
    return v, ' '.join(motivos)

def fmt(x, d=1):
    s = ('%.' + str(d) + 'f') % x
    return s.replace('.', ',')

def espalhar(props):
    grp = defaultdict(list)
    for i, p in enumerate(props): grp[(round(p['lat'], 4), round(p['lon'], 4))].append(i)
    for (lat, lon), idx in grp.items():
        if len(idx) < 2: continue
        for j, i in enumerate(idx):
            r = 0.004 * math.sqrt(j + 1); a = j * 2.39996
            props[i]['lat'] = round(lat + r * math.sin(a), 5)
            props[i]['lon'] = round(lon + r * math.cos(a) / math.cos(math.radians(lat)), 5)

# ---------------------------------------------------------------- pontuação
CDI_LIQ = None
def pontuar(fs):
    """Nota de 0 a 100 por critérios objetivos. Ver site/ranking/index.html (seção Metodologia)."""
    clamp = lambda x, lo=0.0, hi=1.0: max(lo, min(hi, x))
    for f in fs:
        al = []
        # 1) Preço (25): desconto sobre o patrimônio
        pvp = f['pvp'] or 1
        papel = f['tipo'].startswith('Papel')
        if papel:
            s_preco = clamp((1.06 - pvp) / 0.24)  # 1,06 -> 0 ; 0,82 -> 1
        else:
            s_preco = clamp((1.05 - pvp) / 0.35)  # 1,05 -> 0 ; 0,70 -> 1
        if pvp < 0.55:
            s_preco *= 0.5; al.append('Desconto extremo (P/VP %s): o mercado duvida do patrimônio.' % fmt(pvp, 2))
        elif papel and pvp < 0.75:
            s_preco *= 0.8; al.append('Desconto alto para fundo de papel (P/VP %s): o mercado pode estar precificando risco de crédito.' % fmt(pvp, 2))
        # 2) Renda (25): DY 12m e cobertura pelo resultado
        dy = f['dy12'] or 0
        if papel:
            s_dy = clamp((dy - 8) / 4) if dy <= 13.5 else clamp(1 - (dy - 13.5) / 6)  # crédito: DY muito alto = risco alto
        else:
            s_dy = clamp((dy - 7) / 5)
        cob = f['cob']
        s_cob = 0.5 if cob is None else clamp((cob - 0.8) / 0.25)  # 0,80 -> 0 ; 1,05 -> 1
        if cob is not None and cob < 0.92:
            al.append('Pagou mais do que o resultado gerado nos últimos 4 trimestres (%d%% de cobertura).' % round(cob * 100))
        if dy > 16:
            al.append('DY de %s%% muito acima da média: checar se é recorrente.' % fmt(dy)); s_dy *= 0.5
        s_renda = 0.55 * s_dy + 0.45 * s_cob
        # 3) Qualidade (30)
        partes = []
        alav = f['alav'] or 0
        partes.append(clamp((35 - alav) / 30))  # 5% -> 1 ; 35% -> 0
        if alav > 25: al.append('Passivo de %s%% do ativo.' % fmt(alav))
        if f['tipo'] in ('Tijolo', 'Híbrido') and f.get('area'):
            vac = f.get('vac_fis') or 0; inad = f.get('inad') or 0; conc = f.get('conc_max') or 100
            partes += [clamp((20 - vac) / 18), clamp((8 - inad) / 8), clamp((60 - conc) / 50), clamp(math.log10(max(f.get('n_imoveis', 1), 1)) / 1.3)]
            if vac >= 15: al.append('Vacância física de %s%%.' % fmt(vac))
            if inad >= 5: al.append('Inadimplência de %s%% da receita.' % fmt(inad))
            if conc >= 40: al.append('Um imóvel responde por %s%% da receita.' % fmt(conc))
            cap = f.get('cap_contabil')
            if cap is not None:
                partes.append(clamp((cap - 5.5) / 3))  # 5,5% -> 0 ; 8,5% -> 1
                if cap < 6.5: al.append('Aluguel rende só %s%% ao ano sobre o valor contábil dos imóveis.' % fmt(cap))
        if 'via outro FII' in f['tipo']:
            partes.append(0.5); al.append('Investe por meio de outro FII (estrutura master/feeder): os imóveis ou o crédito estão no fundo investido.')
        if f['tipo'] == 'Tijolo' and not f.get('area'):
            partes.append(0.3); al.append('Sem dados de imóveis no informe trimestral da CVM.')
        if f['tipo'] in ('Papel', 'Híbrido'):
            if f.get('n_cri'):
                partes += [clamp(math.log10(f['n_cri']) / 1.6), clamp((25 - (f.get('conc_cri') or 100)) / 20)]
                if (f.get('conc_cri') or 0) >= 20: al.append('Um CRI concentra %s%% da carteira de crédito.' % fmt(f['conc_cri']))
            else:
                partes.append(0.5)
        s_qual = sum(partes) / len(partes)
        if papel:
            s_qual *= 0.85  # qualidade de crédito não é observável nos dados públicos
        # 4) Liquidez e base (10)
        s_liq = 0.6 * clamp((math.log10(max(f['liq'], 1)) - 5.7) / 1.6) + 0.4 * clamp((math.log10(max(f['cotistas'], 1)) - 3.7) / 1.8)
        if f['liq'] < 1.5e6: al.append('Liquidez baixa (R$ %s mil/dia).' % fmt(f['liq'] / 1e3, 0))
        # 5) Custo (10)
        ta = f['taxa_adm']
        s_custo = 0.5 if ta is None else clamp((0.12 - ta) / 0.08)  # taxa mensal %: 0,04 -> 1 ; 0,12 -> 0
        f['s'] = dict(preco=round(s_preco * 25, 1), renda=round(s_renda * 25, 1), qualidade=round(s_qual * 30, 1), liquidez=round(s_liq * 10, 1), custo=round(s_custo * 10, 1))
        f['score'] = round(sum(f['s'].values()), 1)
        f['alertas'] = al
    for i, f in enumerate(sorted(fs, key=lambda f: -f['score'])):
        f['rank'] = i + 1
    # elegibilidade ao Top 20: investível por pessoa física sem sinal de estresse
    n = 0
    for f in sorted(fs, key=lambda f: -f['score']):
        motivos = []
        if f['liq'] < 1.5e6: motivos.append('liquidez abaixo de R$ 1,5 mi/dia')
        if f['cotistas'] < 15000: motivos.append('menos de 15 mil cotistas')
        if (f['pvp'] or 1) < 0.55: motivos.append('desconto extremo')
        if (f['dy12'] or 0) > 20: motivos.append('DY acima de 20%')
        if f['cob'] is not None and f['cob'] < 0.85: motivos.append('dividendo não coberto pelo resultado')
        f['eleg'] = not motivos; f['ineleg'] = motivos
        if f['eleg'] and n < 20:
            n += 1; f['top'] = n
        else:
            f['top'] = None

if __name__ == '__main__':
    main()
