import sc2reader
import csv
import os
import sys
import scouting_detector
import scouting_stats
from multiprocessing import Pool
import argparse
import time
import math
from sc2reader.engine.plugins import SelectionTracker, APMTracker
from selection_plugin import ActiveSelection

sc2reader.engine.register_plugin(APMTracker())
sc2reader.engine.register_plugin(SelectionTracker())
sc2reader.engine.register_plugin(ActiveSelection())

def generateFields(filename):
    try:
        # skipping non-replay files in the directory
        if filename[-9:] != "SC2Replay":
            raise RuntimeError()

        # extracting the game id and adding the correct tag
        # pathname = "practice_replays/" + filename
        pathname = "/Accounts/awb/pattern-analysis/starcraft/replays/" + filename
        game_id = filename.split("_")[1].split(".")[0]
        if filename.startswith("ggg"):
            game_id = "ggg-" + game_id
        elif filename.startswith("spawningtool"):
            game_id = "st-" + game_id
        elif filename.startswith("dropsc"):
            game_id = "ds-" + game_id

        # loading the replay
        try:
            r = sc2reader.load_replay(pathname)
        except:
            print(filename + " cannot load using sc2reader due to an internal ValueError")
            raise RuntimeError()

        # collecting stats and values
        analysis_dict = scouting_detector.scouting_analysis(r)
        team1_rank, team1_rel_rank, team2_rank, team2_rel_rank = scouting_stats.ranking_stats(r)

        # removing replays with flags
        for i in range(1, 3):
            list = analysis_dict[i]
            for item in list:
                if item == -1:
                    print(filename + " contains flags from scouting analysis")
                    raise RuntimeError()

        team1_uid = r.players[0].detail_data['bnet']['uid']
        team2_uid = r.players[1].detail_data['bnet']['uid']

        team1_list = [game_id, team1_uid, team1_rank] + analysis_dict[1]
        team2_list = [game_id, team2_uid, team2_rank] + analysis_dict[2]

        # creating the fields based on who won
        if r.winner.number == 1:
            fields = team1_list + [1] + team2_list + [0]
        elif r.winner.number == 2:
            fields = team1_list + [0] + team2_list + [1]
        return fields
    except:
        return


def writeToCsv():
    files = []
    games = open("valid_game_ids.txt", 'r')
    for line in games:
        files.append(line.strip())
    games.close()

    with open("scouting_analysis.csv", 'w', newline='') as my_csv:
        events_out = csv.DictWriter(my_csv, fieldnames=["GameID", "UID", "Rank", "Category",
                                         "InitialScouting", "BaseScouting", "NewAreas",
                                         "BetweenBattles", "Win"])
        events_out.writeheader()

        pool = Pool(20)
        results = pool.map(generateFields, files)
        pool.close()
        pool.join()

        for fields in results:
            if fields: # generateFields will return None for invalid replays
                # writing 1 line to the csv for each player and their respective stats
                events_out.writerow({"GameID": fields[0], "UID": fields[1], "Rank": fields[2],
                                    "Category": fields[3], "InitialScouting": fields[4],
                                    "BaseScouting": fields[5], "NewAreas": fields[6],
                                    "BetweenBattles": fields[7], "Win": fields[8]})
                events_out.writerow({"GameID": fields[9], "UID": fields[10], "Rank": fields[11],
                                    "Category": fields[12], "InitialScouting": fields[13],
                                    "BaseScouting": fields[14], "NewAreas": fields[15],
                                    "BetweenBattles": fields[16], "Win": fields[17]})

if __name__ == "__main__":
    t1 = time.time()
    writeToCsv()
    deltatime = time.time()-t1
    print("Run time: ", "{:2d}".format(int(deltatime//60)), "minutes and", "{:05.2f}".format(deltatime%60), "seconds")