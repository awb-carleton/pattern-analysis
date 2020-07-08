#tracking battles/engagements

import sc2reader
from collections import defaultdict
import os

def buildBattleList(replay):
    #unable to compute battles for pre 2.0.7
    if replay.build < 25446:
        return 0

    #initializing the list of battles, where each battle is a tuple that contains
    #the frame that the battle began and the frame that the battle ended
    battles = []

    MAX_DEATH_SPACING_FRAMES = 160.0 #max number of frames between deaths for
    #deaths to be considered part of the same engagement

    INTERESTING_ENGAGEMENT = 0.1 #threshold of army that must be killed in order
    #for the engagement to be considered a battle

    owned_units = []
    killed_units = [] #used to determine death of units as an identifier for battles
    for obj in replay.objects.values():
        if obj.owner is not None:
            if (replay.build >= 25446 or obj.is_army) and obj.minerals is not None and obj.finished_at is not None:
                owned_units.append(obj)
                if obj.died_at is not None:
                    killed_units.append(obj)

    #sorted by frame each unit died at
    killed_units = sorted(killed_units, key=lambda obj: obj.died_at)

    engagements = []
    dead_units = []
    current_engagement = None

    #building the list of engagements
    for unit in killed_units:
        if(unit.killing_player is not None or replay.build<25446) and (unit.minerals + unit.vespene > 0):
            dead = unit
            dead_units.append(dead)
            #create a new engagement
            if current_engagement is None or (dead.died_at - current_engagement[2] > MAX_DEATH_SPACING_FRAMES):
                current_engagement = [[dead], dead.died_at, dead.died_at]
                engagements.append(current_engagement)
            #add information to current engagement
            else:
                current_engagement[0].append(dead)
                current_engagement[2] = dead.died_at

    #calculating the loss for each engagement and adding it to the list of
    #battles if greater than 10% of a team's army value is destroyed
    for engagement in engagements:
        killed = defaultdict(int)
        units_at_start = defaultdict(int)
        born_during_battle = defaultdict(int)
        killed_econ = defaultdict(int)
        #calculating loss for each team
        for dead in engagement[0]:
            deadvalue = dead.minerals + dead.vespene
            if dead.is_army:
                killed[dead.owner.team] += deadvalue
            elif replay.build >= 25446:
                killed_econ[dead.owner.team] += deadvalue

        #differentiating between units that were born before vs. during battle
        for unit in owned_units:
            #units born before battle
            if unit.finished_at < engagement[1]:
                units_at_start[unit.owner.team] += unit.minerals + unit.vespene
            #units born during battle
            elif unit.finished_at >= engagement[1] and unit.finished_at < engagement[2]:
                born_during_battle[unit.owner.team] += unit.minerals + unit.vespene

        #deciding whether an engagement meets the threshold to be a battle
        if engagement[2] > engagement[1]:
            for team in replay.teams:
                if(units_at_start[team] > 0) and ((float(killed[team] + killed_econ[team])/(units_at_start[team] + born_during_battle[team])) > INTERESTING_ENGAGEMENT):
                    #greater than 10% of a team's army value was killed, add to battles
                    tuple = (engagement[1], engagement[2])
                    if tuple not in battles:
                        battles.append(tuple)

    return battles

def toTime(battles, frames, seconds):
    timeList = []

    for i in range(len(battles)-1):
        startframe = battles[i][0]
        endframe = battles[i][1]
        starttime = (startframe/frames)*seconds
        endtime = (endframe/frames)*seconds
        startminStr = "{:2d}".format(int(starttime//60))
        startsecStr = "{:05.2f}".format(starttime%60)
        starttimeStr = startminStr + ":" + startsecStr
        endminStr = "{:2d}".format(int(endtime//60))
        endsecStr = "{:05.2f}".format(endtime%60)
        endtimeStr = endminStr + ":" + endsecStr
        battletime = "Battle #{} starts at {} and ends at {}".format(i, starttimeStr, endtimeStr)
        timeList.append(battletime)
    return timeList

def printTime(timeList):
    for battletime in timeList:
        print(battletime)

def main():
    folder = "mini_replays"
    files = os.listdir(folder)
    for filename in files:
        pathname = folder + "/" + filename
        r = sc2reader.load_replay(pathname)
        battles = buildBattleList(r)
        time = toTime(battles, r.frames, r.length.seconds)
        print("\n\n---{}---".format(filename))
        printTime(time)


#main()