import io
from collections import defaultdict

import rows

from brazil_data.cities import get_city_info
from covid19 import google_data
from covid19.exceptions import SpreadsheetValidationErrors
from covid19.models import StateSpreadsheet
from covid19.spreadsheet_validator import TOTAL_LINE_DISPLAY, UNDEFINED_DISPLAY


def get_state_data_from_db(state):
    """Return all state cases from DB, grouped by date"""
    cases, reports = {}, {}
    spreadsheets = StateSpreadsheet.objects.deployable_for_state(state, avoid_peer_review_dupes=False)
    for spreadsheet in spreadsheets:
        date = spreadsheet.date
        # TODO: test 1: quando para a data no estado só tem a planilha de total,
        # devolve apenas o valor total
        # TODO: test 2: quando para a data no estado tem a planilha de total e outras
        # de município, devolve os dados completos usando as informações mais novas.
        # TODO: test 2a: planilha total mais atualizada que deployed (total + deployed)
        # TODO: test 2b: planilha deployed mais atualizada que total (deployed)
        if date in cases:
            # TODO: se a data já estiver no cases mas for só total e essa
            # spreadsheet não for total, não pular (mas só sobrescrever dados
            # do município)
            continue

        # Group all notes for a same URL to avoid repeated entries for date/url
        report_data = reports.get(date, defaultdict(list))
        for url in spreadsheet.boletim_urls:
            report_data[url].append(spreadsheet.boletim_notes)
        reports[date] = report_data

        cases[date] = {}
        for row in spreadsheet.data["table"]:
            city = row["city"]
            if city is None:
                city = TOTAL_LINE_DISPLAY
            cases[date][city] = {
                "confirmed": row["confirmed"],
                "deaths": row["deaths"],
            }

    # reports entries should be returned as a list
    reports_as_list = []
    for date, urls in reports.items():
        for url, notes in urls.items():
            reports_as_list.append(
                {"date": date, "url": url, "notes": "\n".join([n.strip() for n in notes if n.strip()])}
            )

    return {
        "reports": reports_as_list,
        "cases": cases,
    }


def merge_state_data(state):
    gs_data = google_data.get_base_data()[state]  # Get data from old Google Spreadsheets
    original_reports = gs_data["reports"]
    original_cases = gs_data["cases"]

    db_data = get_state_data_from_db(state)  # Get data from database
    new_reports = db_data["reports"]
    new_cases = db_data["cases"]

    # Update original reports (GS) with new ones (DB), removing GS reports for
    # dates which show up in DB.
    new_reports_dates = set(row["date"] for row in new_reports)
    original_reports_filtered = [row for row in original_reports if row["date"] not in new_reports_dates]
    final_reports = original_reports_filtered + new_reports

    # Update original cases (GS) with new ones (DB), overwritting GS cases for
    # dates which show up in DB.
    final_cases = []
    original_data_errors = SpreadsheetValidationErrors()
    for row in original_cases:
        row = row.copy()
        city = row["municipio"]
        if city not in [TOTAL_LINE_DISPLAY, UNDEFINED_DISPLAY]:
            city_info = get_city_info(city, state)
            if city_info:
                city = city_info.city
            else:
                msg = f'Nome inválido de cidade "{city}" na planilha do Google.'
                original_data_errors.new_error(msg)

        for date, values_for_date in new_cases.items():
            date_str = f"{date.day:02d}_{date.month:02d}"
            city_on_date = values_for_date.get(city, {})
            row[f"confirmados_{date_str}"] = city_on_date.get("confirmed", None)
            row[f"mortes_{date_str}"] = city_on_date.get("deaths", None)
        final_cases.append(row)

    ordered_cases = []
    for row in final_cases:
        ordered_cases.append(row_with_sorted_columns(row))

    original_data_errors.raise_if_errors()
    return {"reports": final_reports, "cases": ordered_cases}


def row_with_sorted_columns(row):
    row_dates = set()
    for key in row.keys():
        if not key.startswith("confirmados"):
            continue
        label, day, month = key.split("_")
        row_dates.add(f"2020-{int(month):02d}-{int(day):02d}")

    new = {"municipio": row["municipio"]}
    for date_str in sorted(row_dates, reverse=True):
        year, month, day = date_str.split("-")
        confirmed = f"confirmados_{day}_{month}"
        deaths = f"mortes_{day}_{month}"
        new[confirmed] = row[confirmed]
        new[deaths] = row[deaths]

    return new
