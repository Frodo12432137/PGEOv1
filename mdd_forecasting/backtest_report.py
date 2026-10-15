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
        for name, frame in reports.items():
            write_csv(frame, directory / f"{name}.csv")
        write_training_workbook(bundle, reports, directory / "raport_backtestu.xlsx", read_me=note)
    status = "PEŁNY ZAKRES" if scope["kompletny"] else "CZĘŚCIOWY — NIE ZAKOŃCZONO CAŁEGO BACKTESTU"
    text = [f"BACKTEST: {status}", f"Uruchomienia: {scope['gotowe_prognozy']} / {scope['planowane_prognozy']}",
            f"Trening: {scope['trening_od']} – {scope['trening_do']}",
            f"Backtest (origins): {scope['backtest_uruchomienia_od']} – {scope['backtest_uruchomienia_do']}",
            "Model: produkcyjny trening, zamrożony na cały okres backtestu.",
            "Stan końcowy procesu i limit czasu: status_backtestu.json.",
            "Liczba par origin/cel nie jest liczbą unikalnych godzin.",
            "Jednostki energii zgodne z wejściem; WAPE 0,10 oznacza 10%.",
            "Pogoda: tylko wydania dostępne nie później niż origin. Korekty energii: ocena retrospektywna.",
            "", coverage.to_string(index=False)]
    if not summary.empty:
        net = summary.loc[summary.kierunek.eq("BILANS")]
        text.extend(["", "BILANS — wszystkie prognozy poza DST:", net[["oddzial_code", "n", "n_godzin", "pokrycie", "mae", "rmse", "bias", "wape", "ocena_vs_profil"]].to_string(index=False)])
    temporary = directory / "podsumowanie_backtestu.tmp.txt"
    temporary.write_text("\n".join(text), encoding="utf-8")
    temporary.replace(directory / "podsumowanie_backtestu.txt")
    write_html(balance, summary, bundle, scope, directory / "raport_backtestu.html", max_html_origins)


def write_html(balance, summary, bundle, scope, destination, maximum_origins):
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
<section class="panel"><div class="controls">
<label>Oddział<select id="branch"></select></label><label>Uruchomienie / widok<select id="origin"></select></label>
<label>Wielkość<select id="channel"><option value="0">Bilans: pobranie − oddanie</option><option value="1">Pobranie</option><option value="2">Oddanie</option></select></label>
<button onclick="window.print()">Drukuj / zapisz PDF</button></div>
<h2 id="chartTitle"></h2><p class="legend"><b style="border-color:#20364b">Realizacja</b><b style="border-color:#1168dc">Model ML</b><b style="border-color:#da8200">Profil historyczny</b></p>
<svg id="plot" viewBox="0 0 1160 390" role="img" aria-label="Realizacja i prognozy energii"></svg>
<p id="hover" class="muted">Najedź na wykres, aby odczytać wartości i horyzont.</p>
<p class="muted">Czas lokalny Europe/Warsaw. Energia w jednostce pliku źródłowego. Przerwa realizacji oznacza brak danych lub wyłączoną dobę DST. Widok ciągły wybiera najnowszy origin dla każdego celu; statystyki poniżej oceniają wszystkie zlecenia.</p></section>
<section class="panel"><h2>Błędy według oddziałów</h2><p>Wszystkie horyzonty, poza dobami DST. Mniej MAE i RMSE oznacza mniejszy błąd. Dodatni bias oznacza zawyżanie. Porównanie z profilem wykorzystuje wspólne dostępne wiersze.</p><div class="scroll"><table id="metrics"></table></div></section>
<section class="panel"><h2>Model i sposób oceny</h2><p>Ten sam kod uczenia i predykcji co w zwykłym modelu. Jednorazowy trening, potem model zamrożony na cały backtest. Pogoda wybierana według publikacji dostępnych w chwili uruchomienia. Realizacje testowe nie uczestniczą w uczeniu ani strojeniu.</p><pre id="choices"></pre>
<p class="muted">Ważność pogody i porównanie konfiguracji: trening/raport_treningu.xlsx. Dokładne błędy według godzin, dni, horyzontów i pogody: raport_backtestu.xlsx. Pełne prognozy: backtest_bilans.csv i szczegoly_backtestu.csv. Ocena jest retrospektywna na końcowych korektach energii; nie znamy historycznych czasów ich publikacji. Ponowne strojenie po obejrzeniu testu wymaga nowego okresu oceny.</p><p id="limit" class="muted"></p></section>
</main><script id="data" type="application/json">__PAYLOAD__</script><script>
'use strict';
const D=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
const fmt=x=>x===null||!Number.isFinite(x)?'brak':x.toLocaleString('pl-PL',{maximumFractionDigits:2});
const local=t=>new Date(t).toLocaleString('pl-PL',{timeZone:'Europe/Warsaw',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
$('state').textContent=(D.scope.kompletny?'Ukończono wybrany zakres':'WYNIK CZĘŚCIOWY')+' · '+D.scope.gotowe_prognozy+'/'+D.scope.planowane_prognozy+' uruchomień. Stan końcowy i ewentualny limit czasu: status_backtestu.json. Pokrycie oceny sprawdź w tabeli.';
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
let ymin=Math.min(...ys),ymax=Math.max(...ys);const padding=(ymax-ymin)*.08||1;ymin-=padding;ymax+=padding;
const x=t=>88+(t-xs[0])/Math.max(1,xs.at(-1)-xs[0])*1040,y=v=>330-(v-ymin)/(ymax-ymin)*290;
for(let i=0;i<6;i++){let v=ymin+(ymax-ymin)*i/5,yy=y(v);node('line',{x1:88,x2:1128,y1:yy,y2:yy,stroke:'#e1e7ee'});node('text',{x:80,y:yy+4,'text-anchor':'end',fill:'#53677a','font-size':12},fmt(v));}
for(let i=0;i<6;i++){let t=xs[0]+(xs.at(-1)-xs[0])*i/5;node('text',{x:x(t),y:360,'text-anchor':'middle',fill:'#53677a','font-size':12},local(t));}
['#20364b','#1168dc','#da8200'].forEach((color,k)=>{let path='',gap=true;points.forEach(p=>{let v=p[offset+k];if(v===null||!Number.isFinite(v)){gap=true;return;}path+=(gap?'M':'L')+x(p[0])+','+y(v);gap=false;});node('path',{d:path,fill:'none',stroke:color,'stroke-width':k===1?2.8:1.8,'stroke-dasharray':k===2?'6 4':''});});
const marker=node('line',{x1:88,x2:88,y1:30,y2:330,stroke:'#627990',visibility:'hidden'});
svg.onpointermove=e=>{let px=(e.clientX-svg.getBoundingClientRect().left)/svg.getBoundingClientRect().width*1160;let i=Math.max(0,Math.min(points.length-1,Math.round((px-88)/1040*(points.length-1))));let p=points[i];marker.setAttribute('x1',x(p[0]));marker.setAttribute('x2',x(p[0]));marker.setAttribute('visibility','visible');$('hover').textContent=local(p[0])+' · UTC '+new Date(p[0]).toISOString()+' · h='+p.at(-1)+' · realizacja '+fmt(p[offset])+' · ML '+fmt(p[offset+1])+' · profil '+fmt(p[offset+2]);};}
function changeBranch(){options($('origin'),Object.keys(D.series[$('branch').value]));draw();}
$('branch').onchange=changeBranch;$('origin').onchange=draw;$('channel').onchange=draw;changeBranch();
</script></html>'''
