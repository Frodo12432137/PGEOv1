"""Raport historycznych prognoz: CSV, Excel i samodzielny interaktywny HTML."""
from __future__ import annotations

from html import escape
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .branch_diagnostics import READ_ME, diagnostic_tables, weather_coverage, write_training_workbook
from .branch_model import metrics_table


def continuous_view(balance: pd.DataFrame) -> pd.DataFrame:
    """Do wykresu jednej osi czasu: najnowsza dostępna prognoza danego celu."""
    return (balance.sort_values("forecast_origin_utc")
            .drop_duplicates(["oddzial_code", "target_time_utc"], keep="last")
            .sort_values(["oddzial_code", "target_time_utc"]).reset_index(drop=True))


def write_backtest_reports(bundle, details, balance, directory: Path, scope: dict,
                           training_reports: dict, excluded: pd.DataFrame, max_html_origins: int = 200):
    from .branch_cli import write_csv
    eligible_details = details.loc[~details.cel_wylaczony_DST].copy()
    eligible_balance = balance.loc[~balance.cel_wylaczony_DST].copy()
    # Nawet przy braku realizacji zachowujemy oczekiwane wiersze w mianowniku pokrycia.
    # Nieudokumentowana doba DST jest wyłączona z oceny, a prognoza pozostaje w pełnym CSV.
    coverage = balance.groupby(["oddzial_code", "status_oceny"], dropna=False).size().rename("wiersze").reset_index()
    write_csv(balance, directory / "backtest_bilans.csv")
    write_csv(details, directory / "szczegoly_backtestu.csv")
    write_csv(coverage, directory / "pokrycie_backtestu.csv")
    timeline = continuous_view(balance)
    write_csv(timeline, directory / "przebieg_ciagly.csv")
    reports = {}
    if not eligible_balance.empty:
        reports = diagnostic_tables(eligible_details, eligible_balance, bundle["settings"]["timezone"])
        metrics = []
        for direction in ("POBRANIE", "ODDANIE", "BILANS"):
            is_balance = direction == "BILANS"
            frame = eligible_balance if is_balance else eligible_details.loc[eligible_details.kierunek_energii_norm.eq(direction)]
            prefix = "bilans_" if is_balance else ""
            for column, label in [("wartosc_przewidywana", "ML"), ("wartosc_bazowa", "PROFIL_HISTORYCZNY")]:
                part = metrics_table(frame, prefix + "wartosc_rzeczywista", prefix + column, label)
                part["kierunek"] = direction
                metrics.append(part)
        reports.update(metryki_test=pd.concat(metrics, ignore_index=True),
                       pokrycie_pogody=weather_coverage(eligible_details))
    summary = reports.get("bledy_oddzialy", pd.DataFrame())
    note = [
        ("Stan raportu", f"{'PEŁNY ZAKRES' if scope['kompletny'] else 'WYNIK CZĘŚCIOWY'}: "
         f"{scope['gotowe_prognozy']} / {scope['planowane_prognozy']} uruchomień. Stan procesu sprawdź w status_backtestu.json."),
        ("Ocena zewnętrzna", "Model jest zamrożony po treningu. Żadna realizacja z tego backtestu nie trafia do fit(), wyboru pogody, profilu ani parametrów."),
        ("Zakres dat", "BACKTEST_OD/DO oznaczają lokalne daty uruchomień. Ostatnia prognoza sięga dalej o pełny horyzont. Braków realizacji nie zastępujemy zerem."),
        ("Przebieg ciągły", "Wykres czasu wybiera najnowszy origin dla każdego celu. Pełne statystyki oceniają wszystkie pary origin/cel, również powtarzające się cele."),
        ("Doby DST", "Przy domyślnych ustawieniach całe doby 23/25 h są wyłączone z treningu i oceny. Prognozy tych godzin zostają w CSV z jawnym statusem."),
        ("Ważność i strojenie", "Dobór wariantów i ważność pogody są w trening/raport_treningu.xlsx. Dotyczą wewnętrznego podziału wcześniejszej historii, a nie tego backtestu."),
        *[(key, value) for key, value in READ_ME if key not in {"Ocena", "Pokrycie oceny", "Pogoda"}],
        ("Pokrycie", "Podsumowanie obejmuje wszystkie oczekiwane wiersze poza wyłączonymi dobami DST. Statusy braków są w Pokrycie_backtestu. Zero energii jest poprawną realizacją."),
    ]
    if reports:
        reports.update(backtest_bilans=balance, przebieg_ciagly=timeline, pokrycie_backtestu=coverage,
                       zakres_backtestu=pd.DataFrame(scope.items(), columns=["parametr", "wartosc"]),
                       pominiete_dni_zmiany_czasu=excluded,
                       porownanie_wariantow=training_reports["porownanie_wariantow"])
        from .weather_quality import weather_quality_tables
        reports.update(weather_quality_tables(eligible_details, bundle["settings"]["timezone"]))
        for name in ["rekomendacje_modelu", "rekomendacje_pogody", "walidacja_osd", "waznosc_pogody",
                     "odrzucone_pomiary_energii", "podsumowanie_filtra_energii", "progi_odstajacych_energii",
                     "kontrola_filtra_energii", "godziny_wykluczone_filtrem"]:
            if name in training_reports:
                reports[name] = training_reports[name]
        for name in ["kontrola_czasu_pogody", "rozbieznosci_czasu_pogody"]:
            if name in training_reports:
                reports[name] = training_reports[name]
        for name, frame in reports.items():
            write_csv(frame, directory / f"{name}.csv")
        write_training_workbook(bundle, reports, directory / "raport_backtestu.xlsx", read_me=note)
    status = "PEŁNY ZAKRES" if scope["kompletny"] else "CZĘŚCIOWY — NIE ZAKOŃCZONO CAŁEGO BACKTESTU"
    text = [f"BACKTEST: {status}", f"Uruchomienia: {scope['gotowe_prognozy']} / {scope['planowane_prognozy']}",
            f"Trening: {scope['trening_od']} – {scope['trening_do']}",
            f"Backtest (origins): {scope['backtest_uruchomienia_od']} – {scope['backtest_uruchomienia_do']}",
            "Model: produkcyjny trening, zamrożony na cały okres backtestu.",
            "Stan końcowy procesu: status_backtestu.json. Domyślnie bez limitu czasu.",
            "Liczba par origin/cel nie jest liczbą unikalnych godzin.",
            "Jednostki energii zgodne z wejściem; WAPE 0,10 oznacza 10%.",
            "Pogoda: tylko wydania dostępne nie później niż origin. Korekty energii: ocena retrospektywna.",
            "", coverage.to_string(index=False)]
    if not summary.empty:
        net = summary.loc[summary.kierunek.eq("BILANS")]
        text.extend(["", "BILANS — wszystkie prognozy poza DST:", net[["oddzial_code", "n", "n_godzin", "pokrycie", "mae", "rmse", "bias", "wape", "ocena_vs_profil"]].to_string(index=False)])
        text.extend(["", "PARAMETRY ZASTOSOWANE — WYBÓR NA WCZEŚNIEJSZEJ WALIDACJI:",
                     training_reports.get("rekomendacje_modelu", pd.DataFrame()).to_string(index=False),
                     "", "Pogoda: pokrycie/wiek/zakresy w jakosc_pogody_*.csv. Brak obserwacji meteo do pomiaru trafności prognozy pogody."])
    temporary = directory / "podsumowanie_backtestu.tmp.txt"
    temporary.write_text("\n".join(text), encoding="utf-8")
    temporary.replace(directory / "podsumowanie_backtestu.txt")
    write_html(balance, summary, bundle, scope, directory / "raport_backtestu.html", max_html_origins, reports=reports)


def write_html(balance, summary, bundle, scope, destination, maximum_origins, *, reports=None):
    origins = sorted(balance.forecast_origin_utc.unique())
    selected = np.linspace(0, len(origins) - 1, min(len(origins), maximum_origins), dtype=int)
    origins = [origins[int(position)] for position in selected]
    columns = [f"{channel}_{value}" for channel in ("bilans", "pobranie", "oddanie")
               for value in ("wartosc_rzeczywista", "wartosc_przewidywana", "wartosc_bazowa")]

    def series(frame):
        frame = frame.sort_values("target_time_utc")
        # Brak realizacji i DST to null: wykres ma przerwę, nie sztuczne zero.
        values = json.loads(frame[columns].to_json(orient="values"))
        times = (frame.target_time_utc.dt.as_unit("ns").astype("int64") // 1_000_000).to_list()
        return [[int(timestamp), *row, int(horizon)] for timestamp, row, horizon in zip(times, values, frame.horyzont_h)]

    data = {}
    for branch, group in balance.groupby("oddzial_code"):
        runs = {str(origin): series(group.loc[group.forecast_origin_utc.eq(origin)]) for origin in origins}
        data[branch] = {"Ciągły — najnowsza prognoza celu": series(continuous_view(group)), **runs}
    choices = [{"kierunek": name, "model": choice["parameters"], "pogoda": choice["wariant"]}
               for name, choice in bundle["models"].items()]
    payload = {"series": data, "summary": json.loads(summary.to_json(orient="records")),
               "scope": scope, "choices": choices, "origins_shown": len(origins),
               "origins_total": balance.forecast_origin_utc.nunique()}
    payload["tables"] = {name: json.loads(frame.to_json(orient="records")) for name, frame in (reports or {}).items()
                         if name in {"bledy_godziny", "bledy_miesiace", "bledy_horyzont", "bledy_godziny_lacznie",
                                     "bledy_miesiace_lacznie", "bledy_horyzont_lacznie", "jakosc_pogody_zmienne",
                                     "jakosc_pogody_wydania", "rekomendacje_modelu", "rekomendacje_pogody"}}
    payload["tables"]["podsumowanie_filtra_energii"] = json.loads((reports or {}).get("podsumowanie_filtra_energii", pd.DataFrame()).to_json(orient="records"))
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = f"Trening {scope['trening_od']} – {scope['trening_do']} · Uruchomienia {scope['backtest_uruchomienia_od']} – {scope['backtest_uruchomienia_do']}"
    html = HTML.replace("__PAYLOAD__", encoded).replace("__TITLE__", escape(title))
    temporary = destination.with_suffix(".tmp.html")
    temporary.write_text(html, encoding="utf-8")
    temporary.replace(destination)


HTML = r'''<!doctype html><html lang="pl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>PGEOv1 — backtest</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:#152b40;font:15px system-ui,sans-serif}
main{max-width:1320px;margin:32px auto;padding:0 24px}h1{margin-bottom:6px}p{line-height:1.6}
.panel{background:white;padding:24px;border-radius:12px;margin:18px 0;box-shadow:0 3px 15px #142f4910}
.muted{color:#53677a}.notice{background:#fff1cc;border-left:5px solid #b87000;padding:14px}
.controls{display:flex;gap:20px;flex-wrap:wrap}label{display:grid;gap:7px;font-weight:600}
select,button{padding:9px;border:1px solid #c9d4de;border-radius:6px;background:white;color:#152b40}
svg{width:100%;height:auto;touch-action:pan-y}.legend{display:flex;gap:20px;flex-wrap:wrap}.legend b{padding-left:12px;border-left:5px solid}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:right;padding:10px;border-bottom:1px solid #e2e8f0}th:first-child,td:first-child{text-align:left}
.scroll{overflow:auto}pre{white-space:pre-wrap;font:13px monospace}#hover{min-height:28px;font-variant-numeric:tabular-nums}
@media print{body{background:white}main{margin:0}.panel{box-shadow:none;border:1px solid #ddd;break-inside:avoid}button{display:none}}
</style><main>
<h1>PGEOv1 · Backtest prognozy energii</h1><p class="muted">__TITLE__</p>
<div id="state" class="notice"></div>
<p class="notice">Ocena energii obejmuje realizacje po skonfigurowanym filtrze ujemnych i odstających pomiarów. Wykluczone godziny pozostają w pokryciu jako nieocenione, bez zerowania sum OSD. Progi pochodzą z historii przed walidacją; obejrzyj odrzucone_pomiary_energii.csv, bo wysoka wartość może być prawidłowym szczytem.</p>
<details class="panel"><summary>Wykluczenia pomiarów energii według OSD i miesiąca</summary><p class="muted">Obejmuje wczytany zakres treningu i backtestu. Parametry filtra oraz progi są w Excelu.</p><div class="scroll"><table id="energyFilter"></table></div></details>
<section class="panel"><div class="controls">
<label>Oddział<select id="branch"></select></label><label>Uruchomienie / widok<select id="origin"></select></label>
<label>Wielkość<select id="channel"><option value="0">Bilans: pobranie − oddanie</option><option value="1">Pobranie</option><option value="2">Oddanie</option></select></label>
<button onclick="window.print()">Drukuj / zapisz PDF</button></div>
<h2 id="chartTitle"></h2><p class="legend"><b style="border-color:#20364b">Realizacja</b><b style="border-color:#1168dc">Model ML</b><b style="border-color:#da8200">Profil historyczny</b></p>
<svg id="plot" viewBox="0 0 1160 390" role="img" aria-label="Realizacja i prognozy energii"></svg>
<p id="hover" class="muted">Najedź na wykres, aby odczytać wartości i horyzont.</p>
<p class="muted">Czas lokalny Europe/Warsaw. Energia w jednostce pliku źródłowego. Przerwa realizacji oznacza brak danych lub wyłączoną dobę DST. Widok ciągły wybiera najnowszy origin dla każdego celu; statystyki poniżej oceniają wszystkie zlecenia.</p></section>
<section class="panel"><h2>Błędy według oddziałów</h2><p>Wszystkie horyzonty, poza dobami DST. Mniej MAE i RMSE oznacza mniejszy błąd. Dodatni bias oznacza zawyżanie. Porównanie z profilem wykorzystuje wspólne dostępne wiersze.</p><div class="scroll"><table id="metrics"></table></div></section>
<section class="panel"><h2>Godziny, miesiące i horyzont prognozy</h2>
<div class="controls"><label>Zakres<select id="diagnosticScope"><option value="OSD">Wybrany OSD</option><option value="ALL">Wszystkie OSD — błędy pojedynczych prognoz</option></select></label>
<label>Podział<select id="dimension"><option value="godziny">Godzina lokalna 0–23</option><option value="miesiace">Rok i miesiąc</option><option value="horyzont">Wyprzedzenie 1–72 h</option></select></label>
<label>Miara<select id="errorMetric"><option value="mae">MAE</option><option value="rmse">RMSE</option><option value="bias">Bias</option><option value="wape">WAPE (%)</option></select></label></div>
<svg id="errorPlot" viewBox="0 0 1160 340" aria-label="Błędy w czasie"></svg>
<div class="scroll"><table id="errorTable"></table></div>
<h3>Błąd godzinowy każdego OSD</h3><p class="muted">Kolor pokazuje wielkość wybranej miary. Najedź na komórkę po szczegóły. Wszystkie tabele dotyczą wielkości wybranej u góry: bilansu, pobrania lub oddania.</p>
<svg id="hourHeatmap" viewBox="0 0 1160 350" aria-label="Mapa błędów OSD i godzin"></svg>
<p class="muted">Statystyki zbiorcze uśredniają błędy pojedynczych prognoz OSD, bez sumowania ich bilansów. Szczegółowy błąd każdej godziny, ze znakiem i bezwzględny: bledy_kazdej_godziny.csv i arkusz Kazda_godzina.</p></section>
<section class="panel"><h2>Jakość danych pogodowych</h2><p>Kompletność, wiek wydań i kontrola zakresów dla wybranego OSD. Trafność samej prognozy meteorologicznej pozostaje <strong>nieoceniona — brakuje obserwacji meteo</strong>. Błąd energetyczny przy danej pogodzie nie jest błędem prognozy pogody.</p>
<label>Miesiąc<select id="weatherMonth"></select></label><h3>Zmienne</h3><div class="scroll"><table id="weatherVariables"></table></div>
<h3>Dostępność historycznych wydań</h3><div class="scroll"><table id="weatherIssues"></table></div>
<p class="muted">Wiek to czas od wydania do uruchomienia modelu. Osobno podane wyprzedzenie meteorologiczne sięga od wydania do celu. Podejrzane zakresy wymagają sprawdzenia jednostek; raport nie usuwa ani nie poprawia tych danych. Wpływ pogody: arkusze Waznosc_pogody, Blad_a_wiek_pogody i Warunki_pogody.</p></section>
<section class="panel"><h2>Jakie parametry i pogodę wybrać</h2><p>Poniższe konfiguracje wybrano na wcześniejszej walidacji i już zastosowano. Zewnętrzny backtest służy ocenie. Ostrzeżenie oznacza, że ML nie wygrał z profilem na wspólnych danych walidacji lub porównanie było niedostępne.</p>
<div class="scroll"><table id="recommendations"></table></div><h3>Porównanie zestawów pogody na walidacji</h3><div class="scroll"><table id="weatherRecommendations"></table></div>
<p class="muted">Wynik wskazuje najlepszy spośród przetestowanych wariantów. Nie dowodzi optymalności na każdy przyszły okres. Parametry techniczne użytych modeli są niżej i w Excelu. Nowe strojenie po obejrzeniu tego backtestu wymaga nowego okresu testowego.</p></section>
<section class="panel"><h2>Model i sposób oceny</h2><p>Ten sam kod uczenia i predykcji co w zwykłym modelu. Jednorazowy trening, potem model zamrożony na cały backtest. Pogoda wybierana według publikacji dostępnych w chwili uruchomienia. Realizacje testowe nie uczestniczą w uczeniu ani strojeniu.</p><pre id="choices"></pre>
<p class="muted">Ważność pogody i porównanie konfiguracji: trening/raport_treningu.xlsx. Dokładne błędy według godzin, dni, horyzontów i pogody: raport_backtestu.xlsx. Pełne prognozy: backtest_bilans.csv i szczegoly_backtestu.csv. Ocena jest retrospektywna na końcowych korektach energii; nie znamy historycznych czasów ich publikacji. Ponowne strojenie po obejrzeniu testu wymaga nowego okresu oceny.</p><p id="limit" class="muted"></p></section>
</main><script id="data" type="application/json">__PAYLOAD__</script><script>
'use strict';
const D=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
const fmt=x=>x===null||!Number.isFinite(x)?'brak':x.toLocaleString('pl-PL',{maximumFractionDigits:2});
const local=t=>new Date(t).toLocaleString('pl-PL',{timeZone:'Europe/Warsaw',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
$('state').textContent=(D.scope.kompletny?'Ukończono wybrany zakres':'WYNIK CZĘŚCIOWY')+' · '+D.scope.gotowe_prognozy+'/'+D.scope.planowane_prognozy+' uruchomień. Stan końcowy: status_backtestu.json. Pokrycie oceny sprawdź w tabeli.';
$('limit').textContent='HTML pokazuje '+D.origins_shown+' z '+D.origins_total+' origins oraz widok ciągły. Wszystkie wiersze pozostają w CSV. Raport działa bez Internetu.';
$('choices').textContent=JSON.stringify(D.choices,null,2);
function options(element,values){element.replaceChildren();values.forEach(value=>{const o=document.createElement('option');o.value=value;o.textContent=value;element.appendChild(o);});}
options($('branch'),Object.keys(D.series));
function table(){const head=['Oddział','Wielkość','n prognoz','n godzin','Pokrycie','MAE','RMSE','Bias','WAPE','MAE profilu','Poprawa MAE'];
const fields=['oddzial_code','kierunek','n','n_godzin','pokrycie','mae','rmse','bias','wape','mae_profil_wspolne','poprawa_mae_vs_profil'];
const tr=document.createElement('tr');head.forEach(h=>{let th=document.createElement('th');th.textContent=h;tr.appendChild(th);});$('metrics').appendChild(tr);
D.summary.forEach(row=>{let r=document.createElement('tr');fields.forEach(key=>{let cell=document.createElement('td'),v=row[key];cell.textContent=['pokrycie','wape','poprawa_mae_vs_profil'].includes(key)?(v===null?'brak':fmt(v*100)+'%'):(typeof v==='number'?fmt(v):v);r.appendChild(cell);});$('metrics').appendChild(r);});}
table();
let points=[];const ns='http://www.w3.org/2000/svg';
function node(tag,attrs,text){const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!==undefined)n.textContent=text;$('plot').appendChild(n);return n;}
function draw(){points=D.series[$('branch').value][$('origin').value]||[];const c=Number($('channel').value),offset=1+3*c,svg=$('plot');svg.replaceChildren();if(!points.length)return;
$('chartTitle').textContent=$('branch').value+' · '+$('channel').selectedOptions[0].text;
const xs=points.map(p=>p[0]),ys=points.flatMap(p=>p.slice(offset,offset+3)).filter(v=>v!==null&&Number.isFinite(v));if(!ys.length){node('text',{x:100,y:180},'Brak wartości prognoz i realizacji');return;}
let ymin=ys.reduce((a,b)=>Math.min(a,b),Infinity),ymax=ys.reduce((a,b)=>Math.max(a,b),-Infinity);const padding=(ymax-ymin)*.08||1;ymin-=padding;ymax+=padding;
const x=t=>88+(t-xs[0])/Math.max(1,xs.at(-1)-xs[0])*1040,y=v=>330-(v-ymin)/(ymax-ymin)*290;
for(let i=0;i<6;i++){let v=ymin+(ymax-ymin)*i/5,yy=y(v);node('line',{x1:88,x2:1128,y1:yy,y2:yy,stroke:'#e1e7ee'});node('text',{x:80,y:yy+4,'text-anchor':'end',fill:'#53677a','font-size':12},fmt(v));}
for(let i=0;i<6;i++){let t=xs[0]+(xs.at(-1)-xs[0])*i/5;node('text',{x:x(t),y:360,'text-anchor':'middle',fill:'#53677a','font-size':12},local(t));}
['#20364b','#1168dc','#da8200'].forEach((color,k)=>{let path='',gap=true;points.forEach(p=>{let v=p[offset+k];if(v===null||!Number.isFinite(v)){gap=true;return;}path+=(gap?'M':'L')+x(p[0])+','+y(v);gap=false;});node('path',{d:path,fill:'none',stroke:color,'stroke-width':k===1?2.8:1.8,'stroke-dasharray':k===2?'6 4':''});});
const marker=node('line',{x1:88,x2:88,y1:30,y2:330,stroke:'#627990',visibility:'hidden'});
svg.onpointermove=e=>{let px=(e.clientX-svg.getBoundingClientRect().left)/svg.getBoundingClientRect().width*1160;let i=Math.max(0,Math.min(points.length-1,Math.round((px-88)/1040*(points.length-1))));let p=points[i];marker.setAttribute('x1',x(p[0]));marker.setAttribute('x2',x(p[0]));marker.setAttribute('visibility','visible');$('hover').textContent=local(p[0])+' · UTC '+new Date(p[0]).toISOString()+' · h='+p.at(-1)+' · realizacja '+fmt(p[offset])+' · ML '+fmt(p[offset+1])+' · profil '+fmt(p[offset+2]);};}
const T=D.tables||{},channel=()=>['BILANS','POBRANIE','ODDANIE'][Number($('channel').value)];
function addSvg(parent,tag,attrs,text){const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!==undefined)n.textContent=text;parent.appendChild(n);return n;}
function fillTable(id,rows,columns){const table=$(id);table.replaceChildren();const head=document.createElement('tr');columns.forEach(([key,label])=>{const c=document.createElement('th');c.textContent=label;head.appendChild(c);});table.appendChild(head);rows.slice(0,150).forEach(row=>{const r=document.createElement('tr');columns.forEach(([key,label,type])=>{const c=document.createElement('td'),v=row[key];c.textContent=type==='pct'?(v===null||v===undefined?'brak':fmt(v*100)+'%'):(typeof v==='number'?fmt(v):v??'brak');r.appendChild(c);});table.appendChild(r);});}
function renderWeather(){const branch=$('branch').value,month=$('weatherMonth').value,filter=r=>r.oddzial_code===branch&&r.rok_miesiac===month;
fillTable('weatherVariables',(T.jakosc_pogody_zmienne||[]).filter(filter),[['zmienna','Zmienna'],['pokrycie','Pokrycie','pct'],['braki','Braki'],['nieskonczone','Nieskończone'],['podejrzany_zakres','Podejrzane'],['minimum','Min'],['mediana','Mediana'],['maksimum','Max'],['status','Stan']]);
fillTable('weatherIssues',(T.jakosc_pogody_wydania||[]).filter(filter),[['horyzont','Horyzont'],['pokrycie_wydaniami','Wydania as-of','pct'],['brak_wydania_asof','Brak wydania'],['wiek_wydania_mediana_h','Wiek mediana h'],['wiek_wydania_p95_h','Wiek P95 h'],['liczba_wydan_starszych_24h','Wydania >24 h'],['wyprzedzenie_meteo_mediana_h','Wyprzedzenie meteo h']]);}
function renderAnalysis(){const branch=$('branch').value,kind=channel(),dimension=$('dimension').value,metric=$('errorMetric').value,all=$('diagnosticScope').value==='ALL';
fillTable('energyFilter',(T.podsumowanie_filtra_energii||[]).filter(r=>r.oddzial_code===branch&&r.powod!=='POMIAR_NIEWYKLUCZONY'),[['oddzial_code','OSD'],['kierunek_energii_norm','Kierunek'],['rok_miesiac','Rok/miesiąc'],['powod','Powód'],['liczba_pomiarow','Wykluczone pomiary']]);
const key='bledy_'+dimension+(all?'_lacznie':''),field={godziny:'godzina_lokalna',miesiace:'rok_miesiac',horyzont:'horyzont_h'}[dimension];
const rows=(T[key]||[]).filter(r=>r.kierunek===kind&&(all||r.oddzial_code===branch)).sort((a,b)=>typeof a[field]==='number'?a[field]-b[field]:String(a[field]).localeCompare(String(b[field])));
fillTable('errorTable',rows,[[field,'Zakres'],['n','n prognoz'],['n_godzin','n godzin UTC'],['pokrycie','Pokrycie','pct'],['mae','MAE'],['rmse','RMSE'],['bias','Bias'],['wape','WAPE','pct'],['poprawa_mae_vs_profil','Poprawa vs profil','pct']]);
const svg=$('errorPlot');svg.replaceChildren();const valid=rows.filter(r=>r[metric]!==null&&Number.isFinite(r[metric]));if(valid.length){let vals=valid.map(r=>r[metric]*(metric==='wape'?100:1)),lo=Math.min(0,vals.reduce((a,b)=>Math.min(a,b),Infinity)),hi=Math.max(0,vals.reduce((a,b)=>Math.max(a,b),-Infinity));if(hi===lo)hi=lo+1;const y=v=>275-(v-lo)/(hi-lo)*230,x=i=>90+i/Math.max(1,valid.length-1)*1020;
for(let i=0;i<5;i++){let v=lo+(hi-lo)*i/4;addSvg(svg,'line',{x1:90,x2:1110,y1:y(v),y2:y(v),stroke:'#e1e7ee'});addSvg(svg,'text',{x:82,y:y(v)+4,'text-anchor':'end','font-size':12},fmt(v)+(metric==='wape'?'%':''));}
let path='';valid.forEach((r,i)=>{path+=(i?'L':'M')+x(i)+','+y(vals[i]);const dot=addSvg(svg,'circle',{cx:x(i),cy:y(vals[i]),r:4,fill:'#1168dc'});addSvg(dot,'title',{},String(r[field])+': '+fmt(vals[i])+'; n='+r.n);if(i%Math.max(1,Math.ceil(valid.length/12))===0)addSvg(svg,'text',{x:x(i),y:305,'text-anchor':'middle','font-size':12},String(r[field]));});addSvg(svg,'path',{d:path,fill:'none',stroke:'#1168dc','stroke-width':2});}
const heat=$('hourHeatmap');heat.replaceChildren();const branches=Object.keys(D.series),cells=(T.bledy_godziny||[]).filter(r=>r.kierunek===kind),maximum=cells.reduce((max,r)=>Math.max(max,Math.abs(r[metric]||0)),0)||1;heat.setAttribute('viewBox','0 0 1160 '+(60+branches.length*34));
for(let h=0;h<24;h++)addSvg(heat,'text',{x:120+h*41+18,y:22,'text-anchor':'middle','font-size':12},h);
branches.forEach((code,index)=>{addSvg(heat,'text',{x:95,y:53+index*34,'text-anchor':'end','font-size':13},code);for(let h=0;h<24;h++){const row=cells.find(r=>r.oddzial_code===code&&r.godzina_lokalna===h),value=row?.[metric],intensity=value===null||value===undefined?null:Math.abs(value)/maximum;const rect=addSvg(heat,'rect',{x:120+h*41,y:32+index*34,width:37,height:29,rx:3,fill:intensity===null?'#e2e8f0':`hsl(${210-200*intensity},65%,${94-36*intensity}%)`});addSvg(rect,'title',{},code+' godzina '+h+' · '+metric+'='+fmt(value)+' · n='+(row?.n??0));}});
fillTable('recommendations',T.rekomendacje_modelu||[],[['kierunek','Wielkość'],['model','Model'],['wariant_pogody','Pogoda'],['liczba_iteracji','Iteracje'],['walidacja_macro_mae_norm','Błąd walidacji norm.'],['poprawa_vs_profil_walidacja','Poprawa vs profil','pct'],['zalecenie','Wniosek']]);
fillTable('weatherRecommendations',(T.rekomendacje_pogody||[]).filter(r=>kind==='BILANS'||r.kierunek===kind).sort((a,b)=>a.ranking_walidacji-b.ranking_walidacji),[['kierunek','Wielkość'],['wariant','Pogoda'],['najlepszy_backend','Model'],['walidacja_macro_mae_norm','Błąd walidacji norm.'],['poprawa_vs_kalendarz','Poprawa vs kalendarz','pct'],['ranking_walidacji','Ranking']]);renderWeather();}
function changeBranch(){options($('origin'),Object.keys(D.series[$('branch').value]));options($('weatherMonth'),[...new Set((T.jakosc_pogody_zmienne||[]).filter(r=>r.oddzial_code===$('branch').value).map(r=>r.rok_miesiac))].sort());draw();renderAnalysis();}
$('branch').onchange=changeBranch;$('origin').onchange=draw;$('channel').onchange=()=>{draw();renderAnalysis();};['diagnosticScope','dimension','errorMetric'].forEach(id=>$(id).onchange=renderAnalysis);$('weatherMonth').onchange=renderWeather;changeBranch();
</script></html>'''
