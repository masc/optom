# -*- coding: utf-8 -*-
# @package sumocfg
# @cond LICENSE
# ######################################################################################
# # LGPL License                                                                       #
# #                                                                                    #
# # This file is part of the Optimisation of Overtaking Manoeuvres (OPTOM) project.                     #
# # Copyright (c) 2016, Malte Aschermann (malte.aschermann@tu-clausthal.de)            #
# # This program is free software: you can redistribute it and/or modify               #
# # it under the terms of the GNU Lesser General Public License as                     #
# # published by the Free Software Foundation, either version 3 of the                 #
# # License, or (at your option) any later version.                                    #
# #                                                                                    #
# # This program is distributed in the hope that it will be useful,                    #
# # but WITHOUT ANY WARRANTY; without even the implied warranty of                     #
# # MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the                      #
# # GNU Lesser General Public License for more details.                                #
# #                                                                                    #
# # You should have received a copy of the GNU Lesser General Public License           #
# # along with this program. If not, see http://www.gnu.org/licenses/                  #
# ######################################################################################
# @endcond
from __future__ import division
from __future__ import print_function

from optom.common import log

try:
    from lxml import etree
except ImportError:
    try:
        # Python 2.5
        import xml.etree.cElementTree as etree
    except ImportError:
        try:
            # Python 2.5
            import xml.etree.ElementTree as etree
        except ImportError:
            try:
                # normal cElementTree install
                import cElementTree as etree
            except ImportError:
                try:
                    # normal ElementTree install
                    import elementtree.ElementTree as etree
                except ImportError:
                    print("Failed to import ElementTree from any known place")
import yaml
try:
    from yaml import CSafeLoader as SafeLoader, CSafeDumper as SafeDumper
except ImportError:
    from yaml import SafeLoader, SafeDumper
import itertools
import os
import random
import subprocess
import ast
from optom.configuration.configuration import Configuration
from optom.environment.vehicle import Vehicle
from optom.common import visualisation
from optom.common.io import Writer

s_iloop_template = etree.XML("""
    <xsl:stylesheet version= "1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
    <xsl:template match="/">
    <detector>
    <xsl:for-each select="detector/interval/typedInterval">
    <vehicle>
    <xsl:copy-of select="@begin|@end|@type"/>
    </vehicle>
    </xsl:for-each>
    </detector>
    </xsl:template>
    </xsl:stylesheet>""")


def read_iloop_files(p_iloopfiles):
    l_return = {}
    for i_id, i_fname in p_iloopfiles.iteritems():
        l_root = etree.parse(i_fname)
        l_return[i_id] = dict(
            [(i_vehicle.attrib.get("type"), dict([(k, yaml.load(v, Loader=SafeLoader)) for (k, v) in i_vehicle.attrib.iteritems()])) for i_vehicle in etree.XSLT(s_iloop_template)(l_root).iter("vehicle")]
        )
    # safety checks: do we have all vehicles?
    assert len(set([len(v) for v in l_return.itervalues()])) == 1
    return l_return


class SumoConfig(Configuration):

    def __init__(self, p_args, p_netconvertbinary, p_duarouterbinary):
        super(SumoConfig, self).__init__(p_args)

        self._log = log.logger(__name__, p_args.loglevel, p_args.logfile)
        self._writer = Writer(p_args)
        self._netconvertbinary = p_netconvertbinary
        self._duarouterbinary = p_duarouterbinary
        self._forcerebuildscenarios = p_args.forcerebuildscenarios
        self._sumoconfigdir = os.path.join(self.outputdir, "SUMO")
        self._runsdir = os.path.join(self.outputdir, "SUMO", self._runprefix, "runs")
        self._resultsdir = os.path.join(self.outputdir, "SUMO", self._runprefix, "results")

        if not os.path.exists(self._sumoconfigdir):
            os.makedirs(self._sumoconfigdir)

        if not os.path.exists(self._runsdir):
            os.makedirs(self._runsdir)

        if not os.path.exists(self._resultsdir):
            os.makedirs(self._resultsdir)

        if self._forcerebuildscenarios:
            self._log.debug("--force-rebuild-scenarios set -> rebuilding/overwriting scenarios if already present")
        self._onlyoneotlsegment = p_args.onlyoneotlsegment

        # dump configuration
        self._writer.writeYAML(
            {
                "optomversion": self._optomversion,
                "runconfig": self.runconfig,
                "scenarioconfig": self.scenarioconfig,
                "vtypesconfig": self.vtypesconfig
            },
            os.path.join(p_args.outputdir, "SUMO", self._runprefix, "configuration.yaml")
        )

    @property
    def sumoconfigdir(self):
        return self._sumoconfigdir

    @property
    def runsdir(self):
        return self._runsdir

    @property
    def resultsdir(self):
        return self._resultsdir

    def get(self, p_key):
        return self.runconfig.get("sumo").get(p_key)

    def generateScenario(self, p_scenarioname):
        l_destinationdir = os.path.join(self._runsdir, p_scenarioname)
        if not os.path.exists(os.path.join(l_destinationdir)):
            os.mkdir(l_destinationdir)

        l_scenarioconfig = self.scenarioconfig.get(p_scenarioname)
        l_runcfg = self.runconfig

        l_scenarioruns = {
            "scenarioname": p_scenarioname,
            "runs": {}
        }

        l_nodefile = l_scenarioruns["nodefile"] = os.path.join(l_destinationdir, "{}.nod.xml".format(p_scenarioname))
        l_edgefile = l_scenarioruns["edgefile"] = os.path.join(l_destinationdir, "{}.edg.xml".format(p_scenarioname))
        l_netfile = l_scenarioruns["netfile"] = os.path.join(l_destinationdir, "{}.net.xml".format(p_scenarioname))
        l_settingsfile = l_scenarioruns["settingsfile"] = os.path.join(l_destinationdir, "{}.settings.xml".format(p_scenarioname))

        self._generateNodeXML(l_scenarioconfig, l_nodefile, self._forcerebuildscenarios)
        self._generateEdgeXML(l_scenarioconfig, l_edgefile, self._forcerebuildscenarios)
        self._generateSettingsXML(l_scenarioconfig, l_runcfg, l_settingsfile, self._forcerebuildscenarios)
        self._generateNetXML(l_nodefile, l_edgefile, l_netfile, self._forcerebuildscenarios)

        return l_scenarioruns

    def generateRun(self, p_scenarioruns, p_initialsorting, p_run):
        l_scenarioname = p_scenarioruns.get("scenarioname")
        l_scenarioconfig = self.scenarioconfig.get(l_scenarioname)

        l_destinationdir = os.path.join(self._runsdir, p_scenarioruns.get("scenarioname"))
        if not os.path.exists(os.path.join(l_destinationdir)):
            os.mkdir(l_destinationdir)

        l_runcfg = self.runconfig

        if not os.path.exists(os.path.join(l_destinationdir, str(p_initialsorting))):
            os.mkdir(os.path.join(os.path.join(l_destinationdir, str(p_initialsorting))))

        if not os.path.exists(os.path.join(l_destinationdir, str(p_initialsorting), str(p_run))):
            os.mkdir(os.path.join(os.path.join(l_destinationdir, str(p_initialsorting), str(p_run))))

        self._log.debug("Generating SUMO run configuration for scenario %s / sorting %s / run %d", l_scenarioname, p_initialsorting, p_run)

        l_netfile = p_scenarioruns.get("netfile")
        l_settingsfile = p_scenarioruns.get("settingsfile")

        l_additionalfile = os.path.join(l_destinationdir, str(p_initialsorting), str(p_run), "{}.add.xml".format(l_scenarioname))
        l_tripfile = os.path.join(l_destinationdir, str(p_initialsorting), str(p_run), "{}.trip.xml".format(l_scenarioname))
        l_routefile = os.path.join(l_destinationdir, str(p_initialsorting), str(p_run), "{}.rou.xml".format(l_scenarioname))
        l_configfile = os.path.join(l_destinationdir, str(p_initialsorting), str(p_run), "{}.sumo.cfg".format(l_scenarioname))
        #l_tripinfofile = os.path.join(l_destinationdir, str(p_initialsorting), str(p_run), "{}.tripinfo-output.xml".format(l_scenarioname))
        l_ilooppre21file = os.path.join(self._runsdir, l_scenarioname, str(p_initialsorting), str(p_run), "{}.inductionLoop.pre21.xml".format(l_scenarioname))
        l_ilooppost21file = os.path.join(self._runsdir, l_scenarioname, str(p_initialsorting), str(p_run), "{}.inductionLoop.post21.xml".format(l_scenarioname))
        l_iloopexitfile = os.path.join(self._runsdir, l_scenarioname, str(p_initialsorting), str(p_run), "{}.inductionLoop.exit.xml".format(l_scenarioname))
        #l_fcdfile = os.path.join(l_destinationdir, str(p_initialsorting), str(p_run), "{}.fcd-output.xml".format(l_scenarioname))

        l_runcfgfiles = [l_tripfile, l_additionalfile, l_routefile, l_configfile]

        if len(filter(lambda fname: not os.path.isfile(fname), l_runcfgfiles)) > 0:
            self._log.info("Incomplete/non-existing SUMO run configuration for %s, %s, %d -> (re)building", l_scenarioname, p_initialsorting, p_run)
            self._forcerebuildscenarios = True

        self._generateAdditionalXML(l_scenarioconfig, p_initialsorting, p_run, l_scenarioname, l_ilooppre21file, l_ilooppost21file, l_iloopexitfile, l_additionalfile, self._forcerebuildscenarios)
        self._generateConfigXML(l_configfile, l_netfile, l_routefile, l_additionalfile, l_settingsfile, l_runcfg.get("simtimeinterval"), self._forcerebuildscenarios)
        self._generateTripXML(l_scenarioconfig, l_runcfg, p_initialsorting, l_tripfile, self._forcerebuildscenarios)
        self._generateRouteXML(l_netfile, l_tripfile, l_routefile, self._forcerebuildscenarios)

        return {
            "settingsfile": l_settingsfile,
            "additionalfile": l_additionalfile,
            "tripfile": l_tripfile,
            "routefile": l_routefile,
            #"tripinfofile": l_tripinfofile,
            "configfile": l_configfile,
            #"fcdfile": l_fcdfile
            "inductionloopfiles": {
                "pre21": l_ilooppre21file,
                "post21": l_ilooppost21file,
                "exit": l_iloopexitfile,
            }
        }

    def _generateNodeXML(self, p_scenarioconfig, p_nodefile, p_forcerebuildscenarios=False):
        if os.path.isfile(p_nodefile) and not p_forcerebuildscenarios:
            return

        # parameters
        l_length = p_scenarioconfig.get("parameters").get("length")
        l_nbswitches = p_scenarioconfig.get("parameters").get("switches")
        l_segmentlength = l_length / ( l_nbswitches + 1 )

        if self._onlyoneotlsegment:
            l_length = 2*l_segmentlength # two times segment length

        l_nodes = etree.Element("nodes")
        etree.SubElement(l_nodes, "node", attrib={"id": "enter", "x": str(-l_segmentlength), "y": "0"})
        etree.SubElement(l_nodes, "node", attrib={"id": "21start", "x": "0", "y": "0"})
        etree.SubElement(l_nodes, "node", attrib={"id": "21end", "x": str(l_length), "y": "0"})

        # dummy node for easier from-to routing
        etree.SubElement(
            l_nodes,
            "node",
            attrib={
                "id": "exit",
                "x": str(l_length+0.1 if l_nbswitches % 2 == 1 or self._onlyoneotlsegment else l_length+l_segmentlength),
                "y": "0"
            }
        )

        with open(p_nodefile, "w") as f_pnodesxml:
            f_pnodesxml.write(etree.tostring(l_nodes, pretty_print=True))

    def _generateEdgeXML(self, p_scenarioconfig, p_edgefile, p_forcerebuildscenarios=False):
        if os.path.isfile(p_edgefile) and not p_forcerebuildscenarios:
            return

        # parameters
        l_length = p_scenarioconfig.get("parameters").get("length")
        l_nbswitches = p_scenarioconfig.get("parameters").get("switches")
        l_maxspeed = p_scenarioconfig.get("parameters").get("maxSpeed")

        # assume even distributed otl segment lengths
        l_segmentlength = l_length / ( l_nbswitches + 1 )

        # create edges xml
        l_edges = etree.Element("edges")


        # find slowest vehicle speed to be used as parameter for entering lane
        l_lowestspeed = min(
            map(lambda vtype: vtype.get("desiredSpeed"), self.runconfig.get("vtypedistribution").itervalues())
        )

        # Entering edge with one lane, leading to 2+1 Roadway
        etree.SubElement(
            l_edges,
            "edge",
            attrib={
                "id": "enter_21start",
                "from" : "enter",
                "to": "21start",
                "numLanes": "1",
                "speed": str(l_lowestspeed)
            }
        )

        # 2+1 Roadway
        l_21edge = etree.SubElement(
            l_edges,
            "edge",
            attrib={
                "id": "21segment",
                "from": "21start",
                "to": "21end",
                "numLanes": "2",
                "spreadType": "center",
                "speed": str(l_maxspeed)
            }
        )

        # add splits and joins
        l_addotllane = True
        for i_segmentpos in xrange(0,int(l_length),int(l_segmentlength)) \
                if not self._onlyoneotlsegment else xrange(0,int(2*l_segmentlength-1),int(l_segmentlength)):
            etree.SubElement(
                l_21edge,
                "split",
                attrib={
                    "pos": str(i_segmentpos),
                    "lanes": "0 1" if l_addotllane else "0",
                    "speed": str(l_maxspeed)
                }
            )
            self._lastsegmentpos = i_segmentpos #TODO: fix this hack
            l_addotllane ^= True

        # Exit lane
        etree.SubElement(
            l_edges,
            "edge",
            attrib={
                "id": "21end_exit",
                "from": "21end",
                "to": "exit",
                "numLanes": "1",
                "spreadType": "center",
                "speed": str(l_maxspeed)
            }
        )

        with open(p_edgefile, "w") as f_pedgexml:
            f_pedgexml.write(etree.tostring(l_edges, pretty_print=True))

    def _generateAdditionalXML(self, p_scenarioconfig, p_initialsorting, p_run, p_scenarioname, p_ilooppre21file, p_ilooppost21file, p_iloopexitfile, p_additionalfile, p_forcerebuildscenarios):
        if os.path.isfile(p_additionalfile) and not p_forcerebuildscenarios:
            return

        # parameters
        l_length = p_scenarioconfig.get("parameters").get("length")
        l_nbswitches = p_scenarioconfig.get("parameters").get("switches")
        # assume even distributed otl segment lengths
        l_segmentlength = l_length / (l_nbswitches + 1)

        l_additional = etree.Element("additional")
        # place induction loop right before the first split (i.e. end of starting edge)
        #     <inductionLoop id="myLoop1" lane="foo_0" pos="42" freq="900" file="out.xml"/>
        etree.SubElement(
            l_additional,
            "inductionLoop",
            attrib={
                "id": "pre21",
                "lane": "enter_21start_0",
                "pos": str(l_segmentlength-5),
                "friendlyPos": "true",
                "splitByType": "true",
                "freq": "1",
                "file": p_ilooppre21file
            }
        )

        # induction loop at the beginning of last one-lane segment (post21)
        etree.SubElement(
            l_additional,
            "inductionLoop",
            attrib={
                "id": "post21",
                "lane": "21segment.{}_0".format(self._lastsegmentpos-int(l_segmentlength)) if l_nbswitches % 2 == 1 and not self._onlyoneotlsegment else "21segment.{}_0".format(self._lastsegmentpos),
                "pos": str(int(l_segmentlength)) if l_nbswitches % 2 == 1 and not self._onlyoneotlsegment else "0",
                "friendlyPos": "true",
                "splitByType": "true",
                "freq": "1",
                "file": p_ilooppost21file
            }
        )

        # induction loop at the end of last one-lane segment (exit)
        etree.SubElement(
            l_additional,
            "inductionLoop",
            attrib={
                "id": "exit",
                "lane": "21segment.{}_0".format(self._lastsegmentpos) if l_nbswitches % 2 == 1 or self._onlyoneotlsegment else "21end_exit_0",
                "pos": str(int(l_segmentlength-5)),
                "friendlyPos": "true",
                "splitByType": "true",
                "freq": "1",
                "file": p_iloopexitfile
            }
        )

        with open(p_additionalfile, "w") as f_paddxml:
            f_paddxml.write(etree.tostring(l_additional, pretty_print=True))

    ## create sumo config
    def _generateConfigXML(self, p_configfile, p_netfile, p_routefile, p_additionalfile, p_settingsfile, p_simtimeinterval, p_forcerebuildscenarios=False):
        if os.path.isfile(p_configfile) and not p_forcerebuildscenarios:
            return
        assert type(p_simtimeinterval) == list and len(p_simtimeinterval) == 2

        l_configuration = etree.Element("configuration")
        l_input = etree.SubElement(l_configuration, "input")
        etree.SubElement(l_input, "net-file", attrib={"value": p_netfile})
        etree.SubElement(l_input, "route-files", attrib={"value": p_routefile})
        etree.SubElement(l_input, "additional-files", attrib={"value": p_additionalfile})
        etree.SubElement(l_input, "gui-settings-file", attrib={"value": p_settingsfile})
        l_time = etree.SubElement(l_configuration, "time")
        etree.SubElement(l_time, "begin", attrib={"value": str(p_simtimeinterval[0])})

        with open(p_configfile, "w") as f_pconfigxml:
            f_pconfigxml.write(etree.tostring(l_configuration, pretty_print=True))

    def _generateSettingsXML(self, p_scenarioconfig, p_runcfg, p_settingsfile, p_forcerebuildscenarios=False):
        if os.path.isfile(p_settingsfile) and not p_forcerebuildscenarios:
            return

        l_viewsettings = etree.Element("viewsettings")
        etree.SubElement(l_viewsettings, "viewport",
                               attrib={"x": str(p_scenarioconfig.get("parameters").get("length") / 2),
                                       "y": "0",
                                       "zoom": "100"})
        etree.SubElement(l_viewsettings, "delay", attrib={"value": str(p_runcfg.get("sumo").get("gui-delay"))})

        with open(p_settingsfile, "w") as f_pconfigxml:
            f_pconfigxml.write(etree.tostring(l_viewsettings, pretty_print=True))

    def _nextTime(self, p_lambda, p_prevstarttime, p_distribution="poisson"):
        if p_distribution=="poisson":
            return p_prevstarttime+random.expovariate(p_lambda)
        elif p_distribution=="linear":
            return p_prevstarttime+1/p_lambda
        else:
            return p_prevstarttime

    def _createFixedInitialVehicleDistribution(self, p_runcfg, p_scenarioconfig, p_nbvehicles, p_aadt, p_initialsorting, p_vtypedistribution):
        self._log.debug("Create fixed initial vehicle distribution with %s", p_vtypedistribution)
        l_vtypedistribution = list(itertools.chain.from_iterable(
            map(
                lambda (k,v): [k] * int(round(100 * v.get("fraction"))),
                p_vtypedistribution.iteritems()
            )
        ))

        l_vehps = p_aadt / (24*60*60) \
            if not p_runcfg.get("vehiclespersecond").get("enabled") else p_runcfg.get("vehiclespersecond").get("value")

        l_vehicles = map(
            lambda vtype: Vehicle(self.vtypesconfig.get(vtype), p_vtypedistribution.get(vtype).get("speedDev")),
            [random.choice(l_vtypedistribution) for i in xrange(p_nbvehicles)]
        )

        # generate color map for vehicle max speeds
        l_colormap = visualisation.colormap(
            xrange(int(round(p_scenarioconfig.get("parameters").get("maxSpeed")))),
            'jet_r'
        )

        # update colors
        for i_vehicle in l_vehicles:
            i_vehicle.color = l_colormap.to_rgba(i_vehicle.maxspeed)

        # sort speeds according to initialsorting flag
        assert p_initialsorting in ["best", "random", "worst"]

        if p_initialsorting == "best":
            l_vehicles.sort(key=lambda v: v.maxspeed, reverse=True)
        elif p_initialsorting == "worst":
            l_vehicles.sort(key=lambda v: v.maxspeed)

        # assign start time and id to each vehicle
        for i,i_vehicle in enumerate(l_vehicles):
            i_vehicle.provision("vehicle{}".format(i),
                                self._nextTime(l_vehps,
                                               l_vehicles[i-1].starttime if i > 0 else 0,
                                               p_runcfg.get("starttimedistribution")))

        return l_vehicles

    def _generateTripXML(self, p_scenarioconfig, p_runcfg, p_initialsorting, p_tripfile, p_forcerebuildscenarios=False):
        if os.path.isfile(p_tripfile) and not p_forcerebuildscenarios:
            return

        # generate simple traffic demand by considering AADT, Vmax, roadtype etc
        l_aadt = p_scenarioconfig.get("parameters").get("aadt") \
            if not p_runcfg.get("aadt").get("enabled") else p_runcfg.get("aadt").get("value")

        l_timebegin, l_timeend = p_runcfg.get("simtimeinterval")

        # number of vehicles = AADT / [seconds of day] * [scenario time in seconds]
        l_numberofvehicles = int(round(l_aadt / (24*60*60) * (l_timeend - l_timebegin))) \
            if not p_runcfg.get("nbvehicles").get("enabled") else p_runcfg.get("nbvehicles").get("value")

        self._log.debug("Scenario's AADT of %d vehicles/average annual day => %d vehicles for %d simulation seconds",
                        l_aadt, l_numberofvehicles, (l_timeend - l_timebegin))

        l_vehicles = self._createFixedInitialVehicleDistribution(
            p_runcfg,
            p_scenarioconfig,
            l_numberofvehicles,
            l_aadt,
            p_initialsorting,
            p_runcfg.get("vtypedistribution")
        )


        # xml
        l_trips = etree.Element("trips")

        # create a sumo vtype for each vehicle
        for i_vehicle in l_vehicles:

            # filter for relevant attributes
            l_vattr = dict( map( lambda (k, v): (k, str(v)), filter(
                lambda (k, v): k in ["vClass","length","width","height","minGap","accel","decel","speedFactor","speedDev"], i_vehicle.vtype.iteritems()
            )))

            l_vattr["id"] = str(i_vehicle.id)
            l_vattr["color"] = "{},{},{},{}".format(*i_vehicle.color)
            # override parameters speedDev, desiredSpeed, and length if defined in run config
            l_runcfgspeeddev = self.runconfig.get("vtypedistribution").get(l_vattr.get("vClass")).get("speedDev")
            if l_runcfgspeeddev != None:
                l_vattr["speedDev"] = str(l_runcfgspeeddev)

            l_runcfgdesiredspeed = self.runconfig.get("vtypedistribution").get(l_vattr.get("vClass")).get("desiredSpeed")
            l_vattr["maxSpeed"] = str(l_runcfgdesiredspeed) if l_runcfgdesiredspeed != None else str(i_vehicle.getMaxSpeed())

            l_runcfglength = self.runconfig.get("vtypedistribution").get(l_vattr.get("vClass")).get("length")
            if l_runcfglength != None:
                l_vattr["length"] = str(l_runcfglength)

            # fix tractor vClass to trailer
            if l_vattr["vClass"] == "tractor":
                l_vattr["vClass"] = "trailer"
            l_vattr["type"] = l_vattr.get("vClass")

            etree.SubElement(l_trips, "vType", attrib=l_vattr)

        # add trips
        for i_vehicle in l_vehicles:
            etree.SubElement(l_trips, "trip", attrib={
                "id": i_vehicle.id,
                "depart": str(i_vehicle.starttime),
                "from": "enter_21start",
                "to": "21end_exit",
                "type": i_vehicle.id,
                "departSpeed": "max",
            })

        with open(p_tripfile, "w") as f_ptripxml:
            f_ptripxml.write(etree.tostring(l_trips, pretty_print=True))

    ## create net xml using netconvert
    def _generateNetXML(self, p_nodefile, p_edgefile, p_netfile, p_forcerebuildscenarios=False):
        if os.path.isfile(p_netfile) and not p_forcerebuildscenarios:
            return

        l_netconvertprocess = subprocess.check_output(
            [
                self._netconvertbinary,
                "--node-files={}".format(p_nodefile),
                "--edge-files={}".format(p_edgefile),
                "--output-file={}".format(p_netfile)
            ],
            stderr=subprocess.STDOUT
        )
        self._log.debug(l_netconvertprocess)

    def _generateRouteXML(self, p_netfile, p_tripfile, p_routefile, p_forcerebuildscenarios=False):
        if os.path.isfile(p_routefile) and not p_forcerebuildscenarios:
            return

        l_duarouterprocess = subprocess.check_output(
            [
                self._duarouterbinary,
                "-n", p_netfile,
                "-t", p_tripfile,
                "-o", p_routefile
            ],
            stderr=subprocess.STDOUT
        )
        self._log.debug(l_duarouterprocess)



