import csv
import logging
import numpy as np
import pandas as pd
import pprint
import datetime

# Data Sources
beds = pd.read_csv("data/beds.csv")
populations = pd.read_csv("data/populations.csv")
full_timeseries = pd.read_csv('data/timeseries.csv')

# Modeling Assumptions
r0_initial = 2.4
hospitalization_rate = .073
initial_hospitalization_rate = .05
case_fatality_rate = .011
case_fatality_rate_hospitals_overwhelmed = .01
hospital_capacity_change_daily_rate = 1.05
max_hospital_capacity_factor = 2.08
initial_hospital_bed_utilization = .6

model_interval = 4
rolling_intervals_for_current_infected = 3

logging.basicConfig(level=logging.DEBUG)


def get_population(state, country):
    matching_pops = populations[(populations["state"] == state) & (
        populations["country"] == country)]
    return int(matching_pops.iloc[0].at["population"])


def get_beds(state, country):
    matching_beds = beds[(beds["state"] == state) &
                         (beds["country"] == country)]
    beds_per_mille = float(matching_beds.iloc[0].at["bedspermille"])
    return int(round(beds_per_mille * get_population(state, country) / 1000))


def get_snapshot(date, state, country):
    #snapshot_filename = 'data/{}.csv'.format(date.strftime('%m-%d-%Y'))
    #logging.debug('Loading: {}'.format(snapshot_filename))
    #full_snapshot = pd.read_csv(snapshot_filename)
    #filtered_snapshot = full_snapshot[(full_snapshot["Province/State"] == state) & (full_snapshot["Country/Region"] == country)]
    # pprint.pprint(filtered_snapshot)

    # First, attempt to pull the state-level data without aggregating.
    filtered_timeseries = full_timeseries[(full_timeseries["state"] == state) & (
        full_timeseries["country"] == country) & (full_timeseries['date'] == date.strftime('%Y-%m-%d')) & (full_timeseries["county"].isna())]

    pprint.pprint(filtered_timeseries)

    # Switch to aggregating across counties if that returns no cases.
    if int(filtered_timeseries['cases'].sum()) == 0:
          filtered_timeseries = full_timeseries[(full_timeseries["state"] == state) & (
              full_timeseries["country"] == country) & (full_timeseries['date'] == date.strftime('%Y-%m-%d'))]

    pprint.pprint(filtered_timeseries)

    confirmed = 0
    deaths = 0
    recovered = 0

    try:
        #row = filtered_snapshot.iloc[0]
        confirmed = int(filtered_timeseries['cases'].sum())
        deaths = int(filtered_timeseries['recovered'].sum())
        recovered = int(filtered_timeseries['deaths'].sum())
    except IndexError as e:
        pass

    return {'confirmed': confirmed, 'deaths': deaths, 'recovered': recovered}


def forecast_region(state, country, iterations):
    logging.info('Building results for {} in {}'.format(state, country))
    pop = get_population(state, country)
    beds = get_beds(state, country)
    logging.debug('This location has {} beds for {} people'.format(beds, pop))

    logging.debug(
        'Loading daily report from {} days ago'.format(model_interval))

    cols = ['Note',
            'Date',
            'Eff. R0',
            'Beg. Susceptible',
            'New Inf.',
            'Prev. Inf.',
            'Recov. or Died',
            'End Susceptible',
            'Actual Reported',
            'Pred. Hosp.',
            'Cum. Inf.',
            'Cum. Deaths',
            'Avail. Hosp. Beds',
            'S&P 500',
            'Est. Actual Chance of Inf.',
            'Pred. Chance of Inf.',
            'Cum. Pred. Chance of Inf.',
            'R0',
            '% Susceptible']
    rows = []

    previous_confirmed = 0
    previous_ending_susceptible = pop
    previous_newly_infected = 0
    current_infected_series = []
    recovered_or_died = 0
    cumulative_infected = 0
    cumulative_deaths = 0
    available_hospital_beds = round(
        beds * (1 - initial_hospital_bed_utilization), 0)
    original_available_hospital_beds = available_hospital_beds

    # @TODO: See if today's data is already available. If so, don't subtract an additional day.
    # @TODO: Switch back to 1 after testing
    today = datetime.date.today() - datetime.timedelta(days=1)

    snapshot_date = today - \
        datetime.timedelta(days=model_interval *
                           rolling_intervals_for_current_infected)

    logged_overwhelmed = False

    # Step through existing empirical data
    while True:
        if snapshot_date <= today:
            snapshot = get_snapshot(snapshot_date, state, country)
        else:
            snapshot = {'confirmed': None, 'deaths': None, 'recovered': None}

        # Run the model until enough iterations are complete.
        if snapshot_date > today + datetime.timedelta(days=iterations * model_interval):
            break  # @TODO change to work predictively

        pprint.pprint(snapshot)

        # Use an empirical R0, if available. Otherwise, use the default.
        effective_r0 = r0_initial
        if snapshot['confirmed'] is not None and previous_confirmed > 0:
            effective_r0 = snapshot['confirmed'] / previous_confirmed
        previous_confirmed = snapshot['confirmed']

        if previous_newly_infected > 0:
            # If we have previously known cases, use the R0 to estimate newly infected cases.
            newly_infected = previous_newly_infected * \
                effective_r0 * previous_ending_susceptible / pop
        else:
            # We assume the first positive cases were exclusively hospitalized ones.
            actual_infected_vs_tested_positive = 1 / initial_hospitalization_rate  # ~20
            newly_infected = snapshot['confirmed'] * \
                actual_infected_vs_tested_positive

        # Assume infected cases from before the rolling interval have concluded.
        if (len(current_infected_series) >= 4):
            recovered_or_died = recovered_or_died + \
                current_infected_series[-rolling_intervals_for_current_infected-1]

        previously_infected = sum(
            current_infected_series[-rolling_intervals_for_current_infected:])
        cumulative_infected += newly_infected
        predicted_hospitalized = newly_infected * hospitalization_rate

        if (available_hospital_beds > predicted_hospitalized):
            cumulative_deaths += int(round(newly_infected *
                                           case_fatality_rate))
        else:
            if not logged_overwhelmed:
                logging.info('Hospitals in {} overwhelmed on {}'.format(state, snapshot_date))
            logged_overwhelmed = True
            cumulative_deaths += int(round(newly_infected *
                                           (case_fatality_rate_hospitals_overwhelmed + case_fatality_rate_hospitals_overwhelmed)))

        est_actual_chance_of_infection = None
        actual_reported = 0
        if snapshot['confirmed'] is not None:
            est_actual_chance_of_infection = (
                snapshot['confirmed'] / hospitalization_rate * 2) / pop
            actual_reported = int(round(snapshot['confirmed']))

        ending_susceptible = int(round(
            pop - newly_infected - previously_infected - recovered_or_died))

        row = ('',
               snapshot_date,
               round(effective_r0, 2),
               int(previous_ending_susceptible),  # Beginning susceptible
               int(round(newly_infected)),
               int(round(previously_infected)),
               int(round(recovered_or_died)),
               int(round(ending_susceptible)),
               actual_reported,
               int((predicted_hospitalized)),
               int((cumulative_infected)),
               int((cumulative_deaths)),
               int((available_hospital_beds)),
               None,  # S&P 500
               est_actual_chance_of_infection,
               None,
               None,
               None,
               None)
        rows.append(row)

        # Prepare for the next iteration
        current_infected_series.append(newly_infected)
        previous_newly_infected = newly_infected
        previous_ending_susceptible = ending_susceptible

        if available_hospital_beds < max_hospital_capacity_factor * original_available_hospital_beds:
            available_hospital_beds *= hospital_capacity_change_daily_rate
        snapshot_date += datetime.timedelta(days=model_interval)

    # for i in range(0, iterations):
    #    row = ('', snapshot_date, 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S')
    #    rows.append(row)
    #    snapshot_date += datetime.timedelta(days=model_interval)

    forecast = pd.DataFrame(rows, columns=cols)

    pprint.pprint(forecast)
    return forecast

states = populations['state'].tolist()
for state in states:
    forecast = forecast_region(state, 'USA', 25)
    forecast.to_csv(path_or_buf='results/{}.csv'.format(state), index=False)

#forecast_region('New South Wales', 'Australia', 50)
#forecast_region('Queensland', 'Australia', 50)
#forecast = forecast_region('FL', 'USA', 50)

