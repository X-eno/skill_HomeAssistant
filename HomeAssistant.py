import threading
import json
import requests

from datetime import datetime
from dateutil import tz
import pytz

from core.base.model.AliceSkill import AliceSkill
from core.dialog.model.DialogSession import DialogSession
from core.util.Decorators import IntentHandler
from requests import get
from core.util.model.TelemetryType import TelemetryType
from core.util.TelemetryManager import TelemetryManager

class HomeAssistant(AliceSkill):
	"""
	Author: Lazza
	Description: Connect alice to your home assistant
	"""
	DATABASE = {
		'HomeAssistant': [
			'id integer PRIMARY KEY',
			'entityName TEXT NOT NULL',
			'friendlyName TEXT NOT NULL',
			'deviceState TEXT ',
			'ipAddress TEXT',
			'deviceGroup TEXT',
			'deviceType TEXT',
			'uID TEXT'
		]
	}


	# todo Add Ipaddress's
	# todo add further sensor support ?

	def __init__(self):

		self._broadcastFlag = threading.Event()
		self._newSlotValueList = list()
		self._friendlyName = ""
		self._deviceState = ""
		self._entireSensorlist = list()
		self._switchAndGroupList = list()
		self._dbSensorList = list()
		self._grouplist = list()
		self._action = ""
		self._entity = ""
		self._setup: bool = False
		self._sunState = tuple
		self._triggerType = ""
		self._telemetryLogs = list()
		self._IpList = list()
		# IntentCapture Vars
		self._captureUtterances = ""
		self._captureSlotValue = ""
		self._captureSynonym = ""
		self._utteranceID = 0
		self._slotValueID = 0
		self._utteranceList = list()
		self._slotValueList = list()
		self._data = dict()
		self._finalsynonymList = list()

		super().__init__(databaseSchema=self.DATABASE)


	############################### INTENT HANDLERS #############################

	# Alice speaks what Devices she knows about
	@IntentHandler('WhatHomeAssistantDevices')
	def sayListOfDevices(self, session: DialogSession):
		currentFriendlyNameList = self.listOfFriendlyNames()
		activeFriendlyName = list()
		for name in currentFriendlyNameList:
			activeFriendlyName.append(name[0])

		self.endDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='sayListOfDevices', replace=[activeFriendlyName]),
			siteId=session.siteId
		)


	# Used for picking required data from incoming JSON (used in two places)
	def sortThroughJson(self, item):
		if 'IPAddress' in item["attributes"]:
			ipaddress: str = item["attributes"]["IPAddress"]
			deviceName: str = item["attributes"]["friendly_name"]
			editedDeviceName: str = deviceName.replace(' status', '').lower()
			iplist = [editedDeviceName, ipaddress]
			self._IpList.append(iplist)

		if 'device_class' in item["attributes"]:
			sensorType: str = item["attributes"]["device_class"]
			sensorFriendlyName: str = item["attributes"]["friendly_name"]
			sensorFriendlyName = sensorFriendlyName.lower()
			sensorEntity: str = item["entity_id"]
			sensorValue: str = item["state"]

			dbSensorList = [sensorFriendlyName, sensorEntity, sensorValue, sensorType]
			self._dbSensorList.append(dbSensorList)
		try:

			if 'DewPoint' in item["attributes"]["friendly_name"]:
				sensorType: str = 'dewpoint'
				sensorFriendlyName: str = item["attributes"]["friendly_name"]
				sensorFriendlyName = sensorFriendlyName.lower()
				sensorEntity: str = item["entity_id"]
				sensorValue: str = item["state"]

				dbSensorList = [sensorFriendlyName, sensorEntity, sensorValue, sensorType]
				self._dbSensorList.append(dbSensorList)
			if 'Gas' in item["attributes"]["friendly_name"]:
				sensorType: str = 'gas'
				sensorFriendlyName: str = item["attributes"]["friendly_name"]
				sensorFriendlyName = sensorFriendlyName.lower()
				sensorEntity: str = item["entity_id"]
				sensorValue = item["state"]

				dbSensorList = [sensorFriendlyName, sensorEntity, sensorValue, sensorType]
				self._dbSensorList.append(dbSensorList)

			if 'switch.' in item["entity_id"] or 'group.' in item["entity_id"] and item["entity_id"] not in self._switchAndGroupList:
				if 'switch.' in item["entity_id"]:
					switchFriendlyname: str = item["attributes"]["friendly_name"]
					switchFriendlyname = switchFriendlyname.lower()
					switchList = [item["entity_id"], switchFriendlyname, item['state']]
					self._switchAndGroupList.append(switchList)
				else:
					groupFriendlyname: str = item["attributes"]["friendly_name"]
					groupFriendlyname = groupFriendlyname.lower()
					groupList = [item["entity_id"], groupFriendlyname]
					self._grouplist.append(groupList)


		except Exception:
			pass


	@IntentHandler('AddHomeAssistantDevices')
	def addHomeAssistantDevices(self, session: DialogSession):
		if not self.checkConnection():  # If not connected to HA, say so and stop
			self.sayConnectionOffline(session)
			return

		self.endDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='addHomeAssistantDevices'),
			siteId=session.siteId
		)

		# connect to the HomeAssistant API/States to retrieve entity names and values
		header, url = self.retrieveAuthHeader(urlPath='states')
		data = get(url, headers=header).json()

		if self.getConfig('debugMode'):
			self.logDebug(f'!-!-!-!-!-!-!-! **INCOMING JSON PAYLOAD** !-!-!-!-!-!-!-!')
			self.logDebug(f'')
			self.logDebug(f'{data}')
			self.logDebug(f'')
			self.logDebug(f'')

		# delete and existing values in DB so we can update with a fresh list of Devices
		self.deleteAliceHADatabaseEntries()
		self.deleteHomeAssistantDBEntries()

		# Loop through the incoming json payload to grab data that we need
		for item in data:
			if isinstance(item, dict):
				self.sortThroughJson(item=item)

		# Split above and below into other methods to reduce complexity complaint from sonar
		self.processHADataRetrieval()
		# write friendly names to dialogTemplate as slotValues
		self.addSlotValues()

		self._setup = True
		if self._switchAndGroupList:

			self.ThreadManager.doLater(
				interval=5,
				func=self.sayNumberOfDeviceViaThread
			)
		else:
			self.endDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='addHomeAssistantDevicesError'),
				siteId=session.siteId
			)


	# Do the actual switching via here
	@IntentHandler('HomeAssistantAction')
	def homeAssistantSwitchDevice(self, session: DialogSession):
		if not self.checkConnection():
			self.sayConnectionOffline(session)
			return

		if 'on' in session.slotRawValue('OnOrOff') or 'open' in session.slotRawValue('OnOrOff'):
			self._action = "turn_on"  # Set HA compatible on command
		elif 'off' in session.slotRawValue('OnOrOff') or 'close' in session.slotRawValue('OnOrOff'):
			self._action = "turn_off"

		if session.slotValue('switchNames'):
			deviceName = session.slotRawValue('switchNames')
			if self.getConfig('debugMode'):
				self.logDebug(f'!-!-!-!-!-!-!-! **SWITCHING EVENT** !-!-!-!-!-!-!-!')
				self.logDebug(f'')
				self.logDebug(f'I was requested to "{self._action}" the device called "{deviceName}" ')
				debugSwitchId = self.getDatabaseEntityID(identity=deviceName)

				try:
					self.logDebug(f'debugSwitchId = {debugSwitchId["entityName"]}')
				except Exception as e:
					self.logDebug(f' a error occured switching the switch : {e}')

			tempSwitchId = self.getDatabaseEntityID(identity=deviceName)
			self._entity = tempSwitchId['entityName']

		if self._action and self._entity:
			header, url = self.retrieveAuthHeader(urlPath='services/switch/', urlAction=self._action)

			jsonData = {"entity_id": self._entity}
			requests.request("POST", url=url, headers=header, json=jsonData)

			self.endDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='homeAssistantSwitchDevice', replace=[self._action]),
				siteId=session.siteId
			)


	# Get the state of a single device
	@IntentHandler('HomeAssistantState')
	def getDeviceState(self, session: DialogSession):
		if not self.checkConnection():
			self.sayConnectionOffline(session)
			return

		if 'DeviceState' in session.slots:
			entityName = self.getDatabaseEntityID(identity=session.slotRawValue("DeviceState"))

			# get info from HomeAssitant
			header, url = self.retrieveAuthHeader(urlPath='states/', urlAction=entityName["entityName"])
			stateResponce = requests.get(url=url, headers=header)

			data = stateResponce.json()

			entityID = data['entity_id']
			entityState = data['state']
			# add the device state to the database
			self.updateSwitchValueInDB(key=entityID, value=entityState, name=session.slotRawValue("DeviceState"))
			self.endDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='getActiveDeviceState', replace=[session.slotRawValue("DeviceState"), entityState]),
				siteId=session.siteId
			)


	@IntentHandler('HomeAssistantSun')
	def sunData(self, session: DialogSession):
		"""Returns various states of the sun"""
		if not self.checkConnection():
			self.sayConnectionOffline(session)
			return

		# connect to the HomeAssistant API/States to retrieve sun values
		header, url = self.retrieveAuthHeader(urlPath='states')
		data = get(url, headers=header).json()

		# Loop through the incoming json payload to grab the Sun data that we need
		for item in data:

			if isinstance(item, dict) and 'friendly_name' in item["attributes"] and 'Sun' in item["attributes"]['friendly_name']:
				if self.getConfig('debugMode'):
					self.logDebug(f'!-!-!-!-!-!-!-! **SUN DEBUG LOG** !-!-!-!-!-!-!-!')
					self.logDebug(f'')
					self.logDebug(f'The sun JSON is ==> {item}')
					self.logDebug(f'')

				try:
					self._sunState = item["attributes"]['friendly_name'], item["attributes"]['next_dawn'], item["attributes"]['next_dusk'], item["attributes"]['next_rising'], item["attributes"]['next_setting'], item['state']
				except Exception as e:
					self.logDebug(f'Error getting full sun attributes from Home Assistant: {e}')
					return

		request = session.slotRawValue('sunState')
		if 'position' in request:
			horizon = self._sunState[5].replace("_", " the ")
			self.endDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='sayHorizon', replace=[horizon]),
				siteId=session.siteId
			)

		elif 'dusk' in request:
			dateObj = self.makeDateObjFromString(sunState=self._sunState[2])
			result, hours, minutes = self.standard_date(dateObj)

			if result:
				stateType = self.randomTalk(text='nextSunEvent', replace=[request])
				self.saysunState(session=session, state=stateType, result=result, hours=hours, minutes=minutes)

		elif 'sunrise' in request:
			dateObj = self.makeDateObjFromString(sunState=self._sunState[3])
			result, hours, minutes = self.standard_date(dateObj)

			if result:
				stateType = self.randomTalk(text='nextSunEvent', replace=[request])
				self.saysunState(session=session, state=stateType, result=result, hours=hours, minutes=minutes)

		elif 'dawn' in request:
			dateObj = self.makeDateObjFromString(sunState=self._sunState[1])
			result, hours, minutes = self.standard_date(dateObj)

			if result:
				stateType = self.randomTalk(text='nextSunEvent', replace=[request])
				self.saysunState(session=session, state=stateType, result=result, hours=hours, minutes=minutes)

		elif 'sunset' in request:
			dateObj = self.makeDateObjFromString(sunState=self._sunState[4])
			result, hours, minutes = self.standard_date(dateObj)

			if result:
				stateType = self.randomTalk(text='nextSunEvent', replace=[request])
				self.saysunState(session=session, state=stateType, result=result, hours=hours, minutes=minutes)


	@IntentHandler('GetIpOfDevice')
	def returnIpAddressOfDevice(self, session: DialogSession):
		tableRowvalue = session.slotRawValue('switchNames')
		requestedRow = self.rowOfRequestedDevice(friendlyName=tableRowvalue)
		ipOfDevice: str = ''
		if requestedRow:
			for item in requestedRow:
				ipOfDevice = item['ipAddress']

		else:
			self.endDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='sayIpError', replace=[session.slotRawValue("switchNames")]),
				siteId=session.siteId
			)
			self.logWarning(f'Getting device IP failed: I may not have that data available from HA  - {session.slotRawValue("switchNames")}')
			return

		if ipOfDevice:

			self.endDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='sayIpAddress', replace=[ipOfDevice]),
				siteId=session.siteId
			)
			self.logInfo(f'You can view the {session.slotRawValue("switchNames")} at ->> http://{ipOfDevice}')

		else:
			self.endDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='sayIpError2', replace=[session.slotRawValue("switchNames")]),
				siteId=session.siteId
			)
			self.logWarning(f'Device name not available, HA may not of supplied that device IP')

	# device was asked to switch from Myhome
	def deviceClicked(self, uid):
		if not self.checkConnection():
			return

		switchId = self.getHeatbeatDeviceRow(uid=uid)

		tempID = switchId['entityName']
		tempValue = switchId['deviceState']

		if "on" in tempValue or "open" in tempValue:
			self._action = 'turn_off'
		elif "off" in tempValue or "close" in tempValue:
			self._action = 'turn_on'

		header, url = self.retrieveAuthHeader(urlPath='services/switch/', urlAction=self._action)

		jsonData = {"entity_id": tempID}
		requests.request("POST", url=url, headers=header, json=jsonData)
		self.updateDBStates()


	##################### POST AND GET HANDLERS ##############################

	def updateDBStates(self):
		"""Update entity states from a 5 min timer"""
		header, url = self.retrieveAuthHeader(urlPath='states')
		data = get(url, headers=header).json()

		# Loop through the incoming json payload to grab data that we need
		for item in data:

			if isinstance(item, dict):
				self.sortThroughJson(item=item)

		if self.getConfig('debugMode'):
			self.logDebug(f'!-!-!-!-!-!-!-! **updateDBStates code** !-!-!-!-!-!-!-!')

		for switchItem, name, state in self._switchAndGroupList:

			# Locate entity in HA database and update it's state
			deviceID = self.getDatabaseEntityID(identity=name)
			if deviceID:
				self.DeviceManager.onDeviceHeartbeat(deviceID['uID'])
				if self.getConfig('debugMode'):
					self.logDebug(f'')
					self.logDebug(f'I\'m updating the "{switchItem}" with state "{state}" ')

				self.updateSwitchValueInDB(key=switchItem, value=state, name=name)
		# reset this object to prevent multiple list values
		self._switchAndGroupList = list()

		for sensorName, entity, state, haClass in self._dbSensorList:

			# Locate sensor in the database and update it's value
			if self.getDatabaseEntityID(identity=sensorName):
				if self.getConfig('debugMode'):
					self.logDebug(f'')
					self.logDebug(f'I\'m now updating the SENSOR "{sensorName}" with the state of "{state}" ')
					self.logDebug(f'HA class is "{haClass}" ')
					self.logDebug(f'The entity ID is "{entity}"')

				self.updateSwitchValueInDB(key=entity, value=state, name=sensorName)
		# reset object value to prevent multiple items each update
		self._dbSensorList = list()


	def retrieveAuthHeader(self, urlPath: str, urlAction: str = None):
		"""
		Sets up and returns the Request Header file and url

		:param urlPath - sets the path such as services/Switch/
		:param urlAction - sets the action such as turn_on or turn_off

		EG: useage - header, url = self.requestAuthHeader(urlPath='services/switch/', urlAction=self._action)
		:returns: header and url
		"""

		header = {"Authorization": f'Bearer {self.getConfig("haAccessToken")}', "content-type": "application/json", }

		if urlAction:
			url = f'{self.getConfig("haIpAddress")}{urlPath}{urlAction}'
		else:  # else is used for checking HA connection and boot up
			url = f'{self.getConfig("haIpAddress")}{urlPath}'

		return header, url


	def checkConnection(self) -> bool:
		try:
			header, url = self.retrieveAuthHeader(' ', ' ')
			response = get(self.getConfig('haIpAddress'), headers=header)
			if '{"message": "API running."}' in response.text:
				return True
			else:
				self.logWarning(f'It seems HomeAssistant is currently not connected ')
				return False
		except Exception as e:
			self.logWarning(f'HomeAssistant connection failed with an error: {e}')
			return False


	########################## DATABASE ITEMS ####################################


	def AddToAliceDB(self, uID: str, friendlyName: str, deviceType: int):
		"""Add devices to Alices Devicemanager-Devices table.
		If location not known, create and store devices in a StoreRoom"""

		values = {'typeID': deviceType, 'uid': uID, 'locationID': self.LocationManager.getLocation(location='StoreRoom').id, 'name': friendlyName, 'display': "{'x': '10', 'y': '10', 'rotation': 0, 'width': 45, 'height': 45}", 'skillName': 'HomeAssistant'}
		self.DatabaseManager.insert(tableName=self.DeviceManager.DB_DEVICE, values=values, callerName=self.DeviceManager.name)


	def addEntityToHADatabase(self, entityName: str, friendlyName: str, deviceState: str = None, ipAddress: str = None, deviceGroup: str = None, deviceType: str = None, uID: str = None):
		# adds sensor data to the HomeAssistant database
		# noinspection SqlResolve
		self.databaseInsert(
			tableName='HomeAssistant',
			query='INSERT INTO :__table__ (entityName, friendlyName, deviceState, ipAddress, deviceGroup, deviceType, uID) VALUES (:entityName, :friendlyName, :deviceState, :ipAddress, :deviceGroup, :deviceType, :uID)',
			values={
				'entityName'  : entityName,
				'friendlyName': friendlyName,
				'deviceState' : deviceState,
				'ipAddress'   : ipAddress,
				'deviceGroup' : deviceGroup,
				'deviceType'  : deviceType,
				'uID'         : uID
			}
		)


	# noinspection SqlResolve
	def deleteAliceHADatabaseEntries(self):
		""""
		 Deletes values from Alice's devices table if name value is HomeAssistant

		"""
		self.DatabaseManager.delete(
			tableName=self.DeviceManager.DB_DEVICE,
			query='DELETE FROM :__table__ WHERE skillName = "HomeAssistant" ',
			callerName=self.DeviceManager.name
		)


	# noinspection SqlResolve
	def deleteHomeAssistantDBEntries(self):
		""" Deletes the entire database table from the Homeassistant Table"""
		# noinspection SqlWithoutWhere
		self.DatabaseManager.delete(
			tableName='HomeAssistant',
			query='DELETE FROM :__table__ ',
			callerName=self.name
		)


	# noinspection SqlResolve
	def getDatabaseEntityID(self, identity):
		"""Get entityName where friendlyName is the same as requested"""

		# returns SensorId for all listings of a friendlyName
		return self.databaseFetch(
			tableName='HomeAssistant',
			query='SELECT entityName, uID FROM :__table__ WHERE friendlyName = :identity',
			method='one',
			values={
				'identity': identity
			}
		)


	# noinspection SqlResolve
	def getHeatbeatDeviceRow(self, uid):
		""" returns the state of a heartbeat compatible  device

		:params uid = Device identification
		"""

		return self.databaseFetch(
			tableName='HomeAssistant',
			query='SELECT entityName, deviceState FROM :__table__ WHERE uID = :uid ',
			values={
				'uid': uid
			}
		)


	# noinspection SqlResolve
	def getSwitchValueFromDB(self, uid, key):
		""" returns the state of the entityName

		:params uid = Device identification
		:params key = the entities name IE - switch.kitchen_light"""

		return self.databaseFetch(
			tableName='HomeAssistant',
			query='SELECT entityName FROM :__table__ WHERE uID = :uid and entityName = :key ',
			method='one',
			values={
				'uid': uid,
				'key': key
			}
		)


	# noinspection SqlResolve
	def listOfFriendlyNames(self):
		"""Returns a list of known friendly names that are switchable devices"""

		return self.databaseFetch(
			tableName='HomeAssistant',
			query='SELECT friendlyName FROM :__table__  WHERE deviceGroup != "sensor" ',
			method='all'
		)


	# noinspection SqlResolve
	def listOfHAuid(self):
		"""Returns a list of known uID's from HA database"""

		return self.databaseFetch(
			tableName='HomeAssistant',
			query='SELECT uID FROM :__table__',
			method='all'
		)


	# noinspection SqlResolve
	def listOfHeartbeatDevices(self):
		"""Returns a list of known uID's from HA database that require a heartbeat"""
		return self.databaseFetch(
			tableName='HomeAssistant',
			query='SELECT * FROM :__table__ WHERE deviceGroup == "switch" or deviceType == "temperature" ',
			method='all'
		)

	# noinspection SqlResolve
	def getSensorValues(self):
		"""Returns a list of known sensors"""

		return self.databaseFetch(
			tableName='HomeAssistant',
			query='SELECT * FROM :__table__  WHERE deviceGroup == "sensor" ',
			method='all'
		)


	# noinspection SqlResolve
	def rowOfRequestedDevice(self, friendlyName: str):
		"""Returns the row for the selected friendlyname

		:params friendlyName is for example kitchen light"""

		return self.databaseFetch(
			tableName='HomeAssistant',
			query='SELECT * FROM :__table__ WHERE friendlyName = :friendlyName ',
			values={'friendlyName': friendlyName},
			method='all'
		)


	# noinspection SqlResolve
	def updateSwitchValueInDB(self, key: str, value: str, name: str = None):
		"""Updates the state of the switch in the selected row of database
		:params key = entityName
		:params name = entity friendly name
		:params value is the new state of the switch"""

		self.DatabaseManager.update(
			tableName='HomeAssistant',
			callerName=self.name,
			query='UPDATE :__table__ SET deviceState = :value WHERE friendlyName = :name and entityName = :key',
			values={
				'value': value,
				'key'  : key,
				'name' : name
			}
		)


	# Future enhancement
	# noinspection SqlResolve
	def updateDeviceIPInfo(self, ip: str, nameIdentity: str):
		"""updates the device with it's Ip address"""

		self.DatabaseManager.update(
			tableName='HomeAssistant',
			callerName=self.name,
			query='UPDATE :__table__ SET ipAddress = :ip WHERE friendlyName = :nameIdentity ',
			values={
				'ip'          : ip,
				'nameIdentity': nameIdentity
			}
		)


	################# General Methods ###################


	def sayNumberOfDeviceViaThread(self):
		currentFriendlyNameList = self.listOfFriendlyNames()
		activeFriendlyName = list()
		for name in currentFriendlyNameList:
			activeFriendlyName.append(name[0])
		listLength = len(activeFriendlyName)
		self.say(
			text=self.randomTalk(text='saynumberOfDevices', replace=[listLength]),
			siteId=self.getAliceConfig('deviceName')
		)


	def sayConnectionOffline(self, session: DialogSession):
		self.endDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='sayConnectionOffline'),
			siteId=session.siteId
		)


	def onFiveMinute(self):
		if not self.checkConnection():
			return

		self.updateDBStates()
		sensorDBrows = self.getSensorValues()

		debugtrigger = 0
		for sensor in sensorDBrows:
			if not 'unavailable' in sensor['deviceState'] and not 'unknown' in sensor['deviceState']:

				newPayload = dict()
				siteID: str = sensor["friendlyName"]
				siteIDlist = siteID.split()

				siteID = siteIDlist[0]
				# clean up siteID and make it all lowercase so less errors when using text widget
				siteID.replace(" ", "").lower()

				self.onFiveMinuteCodeComplexityReducer(sensor=sensor, newPayload=newPayload)

				if newPayload:
					try:
						if self.getConfig('debugMode') and debugtrigger == 0:
							self.logDebug("")
							self.logDebug(f'!-!-!-!-!-!-!-! **Now adding to the Telemetry DataBase** !-!-!-!-!-!-!-!')
							debugtrigger = 1
						self.sendToTelemetry(newPayload=newPayload, siteId=siteID)
					except Exception as e:
						self.logWarning(f'There was a error logging data for sensor {siteID} as : {e}')


	@staticmethod
	def onFiveMinuteCodeComplexityReducer(sensor, newPayload):
		if 'temperature' in sensor["deviceType"]:
			newPayload['TEMPERATURE'] = sensor['deviceState']
		if 'humidity' in sensor["deviceType"]:
			newPayload['HUMIDITY'] = sensor['deviceState']
		if 'pressure' in sensor["deviceType"]:
			newPayload['PRESSURE'] = sensor['deviceState']
		if 'gas' in sensor["deviceType"]:
			newPayload['GAS'] = sensor['deviceState']
		if 'dewpoint' in sensor["deviceType"]:
			newPayload['DEWPOINT'] = sensor['deviceState']
		if 'illuminance' in sensor["deviceType"]:
			newPayload['ILLUMINANCE'] = sensor['deviceState']

		return newPayload


	# add friendlyNames to dialog template as a list of slotValues
	def addSlotValues(self) -> bool:
		"""Add slotValues to the existing dialogTemplate file for the skill"""

		file = self.getResource(f'dialogTemplate/{self.activeLanguage()}.json')
		if not file:
			return False
		friendlylist = self.listOfFriendlyNames()

		# using this duplicate var to capture things like sonoff 4 channel pro or multi button devices
		duplicate = ''

		if self.getConfig('debugMode'):
			self.logDebug('!-!-!-!-!-!-!-! **ADDING THE SLOTVALUE** !-!-!-!-!-!-!-!')

		for valuesToStore in friendlylist:
			if valuesToStore[0] not in duplicate:
				dictValue = {'value': valuesToStore[0]}
				self._newSlotValueList.append(dictValue)

				if self.getConfig('debugMode'):
					self.logDebug('')
					self.logDebug(f'{valuesToStore[0]}')
					self.logDebug('')
			duplicate = valuesToStore

		data = json.loads(file.read_text())

		if 'slotTypes' not in data:
			return False

		data['slotTypes'][0]['values'] = self._newSlotValueList
		file.write_text(json.dumps(data, ensure_ascii=False, indent=4))

		return True


	def processHADataRetrieval(self):
		# extra method to reduce complexity value of addHomeAssistantDevices()
		# clean up any duplicates in the list

		duplicateList = dict((x[0], x) for x in self._switchAndGroupList).values()

		duplicateGroupList = dict((x[0], x) for x in self._grouplist).values()

		duplicateSensorList = dict((x[0], x) for x in self._dbSensorList).values()

		switchTypeID = self.DeviceManager.getDeviceTypeByName("HaSwitch").id

		# process group entities
		for group, value in duplicateGroupList:
			freeGroupUID = self.DeviceManager.getFreeUID()
			self.addEntityToHADatabase(entityName=group, friendlyName=value, uID=freeGroupUID, deviceGroup='group')

		# process Switch entities
		for switchItem in duplicateList:
			freeUID = self.DeviceManager.getFreeUID()
			self.addEntityToHADatabase(entityName=switchItem[0], friendlyName=switchItem[1], deviceState=switchItem[2], uID=freeUID, deviceGroup='switch')

			self.AddToAliceDB(uID=freeUID, friendlyName=switchItem[1], deviceType=switchTypeID)

		# Process Sensor entities
		for sensorItem in duplicateSensorList:
			freeSensorId = self.DeviceManager.getFreeUID()
			if 'temperature' in sensorItem[3]:

				self.AddToAliceDB(uID=freeSensorId, friendlyName=sensorItem[0], deviceType=self.DeviceManager.getDeviceTypeByName("HaSensor").id)

			self.addEntityToHADatabase(entityName=sensorItem[1], friendlyName=sensorItem[0], uID=freeSensorId, deviceState=sensorItem[2], deviceGroup='sensor', deviceType=sensorItem[3])

		# Process Sensor entities
		for deviceDetails in self._IpList:

			self.updateDeviceIPInfo(ip=deviceDetails[1], nameIdentity=deviceDetails[0])


	def sendToTelemetry(self, newPayload: dict, siteId: str):
		# create location if it doesnt exist
		self.LocationManager.getLocation(location=siteId)

		for item in newPayload.items():
			teleType: str = item[0]
			teleType = teleType.upper()

			if self.getConfig('debugMode'):
				self.logDebug(f'')
				self.logDebug(f'The {teleType} reading for the {siteId} is {item[1]} ')

			try:
				if 'TEMPERATURE' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.TEMPERATURE, value=item[1], service=self.name, siteId=siteId)
				elif 'HUMIDITY' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.HUMIDITY, value=item[1], service=self.name, siteId=siteId)
				elif 'DEWPOINT' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.DEWPOINT, value=item[1], service=self.name, siteId=siteId)
				elif 'PRESSURE' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.PRESSURE, value=item[1], service=self.name, siteId=siteId)
				elif 'GAS' in teleType and isinstance(item[1], int) or isinstance(item[1], float):
					self.TelemetryManager.storeData(ttype=TelemetryType.GAS, value=item[1], service=self.name, siteId=siteId)
				elif 'AIR_QUALITY' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.AIR_QUALITY, value=item[1], service=self.name, siteId=siteId)
				elif 'UV_INDEX' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.UV_INDEX, value=item[1], service=self.name, siteId=siteId)
				elif 'NOISE' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.NOISE, value=item[1], service=self.name, siteId=siteId)
				elif 'CO2' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.CO2, value=item[1], service=self.name, siteId=siteId)
				elif 'RAIN' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.RAIN, value=item[1], service=self.name, siteId=siteId)
				elif 'SUM_RAIN_1' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.SUM_RAIN_1, value=item[1], service=self.name, siteId=siteId)
				elif 'SUM_RAIN_24' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.SUM_RAIN_24, value=item[1], service=self.name, siteId=siteId)
				elif 'WIND_STRENGTH' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.WIND_STRENGTH, value=item[1], service=self.name, siteId=siteId)
				elif 'WIND_ANGLE' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.WIND_ANGLE, value=item[1], service=self.name, siteId=siteId)
				elif 'GUST_STREGTH' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.GUST_STRENGTH, value=item[1], service=self.name, siteId=siteId)
				elif 'GUST_ANGLE' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.GUST_ANGLE, value=item[1], service=self.name, siteId=siteId)
				elif 'Illuminance' in teleType:
					self.TelemetryManager.storeData(ttype=TelemetryType.LIGHT, value=item[1], service=self.name, siteId=siteId)

			except Exception as e:
				self.logInfo(f'An exception occured adding {teleType} reading: {e}')


	def onBooted(self) -> bool:

		if 'http://localhost:8123/api/' in self.getConfig("haIpAddress"):
			self.logWarning(f'You need to update the HAIpAddress in Homeassistant Skill ==> settings')
			self.say(
				text=self.randomTalk(text='sayConfigureMe'),
				siteId=self.getAliceConfig('deviceName')
			)
			return False
		else:
			try:
				header, url = self.retrieveAuthHeader(' ', ' ')
				response = get(self.getConfig('haIpAddress'), headers=header)

				if self.getConfig('debugMode'):
					self.logDebug(f'!-!-!-!-!-!-!-! **OnBooted code** !-!-!-!-!-!-!-!')
					self.logDebug(f'')
					self.logDebug(f'{response.text} - onBooted connection code')
					self.logDebug(f' The header is {header} ')
					self.logDebug(f'The Url is {url} ')
					self.logDebug(f'')

				if '{"message": "API running."}' in response.text:
					self.logInfo(f'HomeAssistant Connected')
					uidList = self.listOfHeartbeatDevices()

					if uidList:
						for uid in uidList:
							self.DeviceManager.deviceConnecting(uid=uid['uID'])
					return True

				else:
					self.logWarning(f'Issue connecting to HomeAssistant : {response.text}')

					return False

			except Exception as e:
				self.logWarning(f'HomeAssistant failed to start. Double check your settings in the skill {e}')
				return False


	@property
	def broadcastFlag(self) -> threading.Event:
		return self._broadcastFlag


	@staticmethod
	def makeDateObjFromString(sunState: str):
		"""Takes HA's UTC string and turns it to a datetime object"""

		utcDatetime = datetime.strptime(sunState, "%Y-%m-%dT%H:%M:%S%z")
		utcDatetimeTimestamp = float(utcDatetime.strftime("%s"))
		localDatetimeConverted = datetime.fromtimestamp(utcDatetimeTimestamp)
		return localDatetimeConverted


	@staticmethod
	def standard_date(dt):
		"""
		Takes a naive UTC datetime stamp, Converts it to local timezone,
		Outputs time between the converted UTC date and hours and minutes until then

		   params:
                dt: the date in UTC format to convert (no TZ info).
        """

		now = datetime.now()
		usersTZ = tz.tzlocal()

		# give the naive stamp, timezone info
		utcTimeStamp = dt.replace(tzinfo=pytz.utc)

		# convert from utc to local time
		haConvertedTimestamp = utcTimeStamp.astimezone(usersTZ)
		now = now.astimezone(usersTZ)

		diff = haConvertedTimestamp - now
		timeStampFormat = '%b %d @ %I:%M%p'

		# apply formatting and obtain hours and minute output
		timeDifferenceResult = haConvertedTimestamp.strftime(timeStampFormat)
		days, seconds = diff.days, diff.seconds
		hours = days * 24 + seconds // 3600
		minutes = (seconds % 3600) // 60

		return timeDifferenceResult, hours, minutes


	def saysunState(self, session, state, result, hours, minutes):
		self.endDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='saySunState', replace=[state, result, hours, minutes]),
			siteId=session.siteId
		)


	########################## TELEMTRY PROCESSING #############################

	def onGasAlert(self, **kwargs):
		if self.name in kwargs['service']:
			self._triggerType = 'gas'
			self.telemetryEvents(kwargs)


	def onPressureHighAlert(self, **kwargs):
		if self.name in kwargs['service']:
			self._triggerType = 'pressure'
			self.telemetryEvents(kwargs)


	def onTemperatureHighAlert(self, **kwargs):
		if self.name in kwargs['service']:
			self._triggerType = 'temperature'
			self.telemetryEvents(kwargs)


	def onTemperatureLowAlert(self, **kwargs):
		if self.name in kwargs['service']:
			self._triggerType = 'Temperature'
			self.telemetryEvents(kwargs)


	def onFreezing(self, **kwargs):
		if self.name in kwargs['service']:
			self._triggerType = 'freezing'
			self.telemetryEvents(kwargs)


	def onHumidityHighAlert(self, **kwargs):
		if self.name in kwargs['service']:
			self._triggerType = 'humidity'
			self.telemetryEvents(kwargs)


	def onHumidityLowAlert(self, **kwargs):
		if self.name in kwargs['service']:
			self._triggerType = 'Humidity'
			self.telemetryEvents(kwargs)


	def onCOTwoAlert(self, **kwargs):
		if self.name in kwargs['service']:
			self._triggerType = 'C O 2'
			self.telemetryEvents(kwargs)


	def telemetryEvents(self, kwargs):

		trigger = kwargs['trigger']
		value = kwargs['value']
		threshold = kwargs['threshold']
		area = kwargs['area']

		if 'upperThreshold' in trigger:
			trigger = 'high'
		else:
			trigger = 'low'

		if not 'freezing' in self._triggerType:
			self.say(
				text=self.randomTalk(text='sayTelemetryAlert', replace=[self._triggerType, trigger, threshold, value, area]),
				siteId=self.getAliceConfig('deviceName')
			)
		else:
			self.say(
				text=self.randomTalk(text='sayTelemetryFreezeAlert', replace=[area, value]),
				siteId=self.getAliceConfig('deviceName')
			)


	###############  Telemetry Logging Data  #################
	# work in progress
	# noinspection SqlResolve
	def getTelemetryLogs(self):
		telemetryDblogs = TelemetryManager()
		self._telemetryLogs = telemetryDblogs.databaseFetch(
			tableName='telemetry',
			query='SELECT * FROM :__table__ ORDER BY timestamp DESC LIMIT 200',
			method='all'
		)

		self.seperateTelemetryLogs()


	def seperateTelemetryLogs(self):
		temperatureLogData = list()
		for sensor in self._telemetryLogs:
			if 'temperature' in sensor['type']:
				temperatureLogs = [sensor['type'], sensor['value'], sensor['siteId'], sensor['timestamp']]
				temperatureLogData.append(temperatureLogs)

		print(f'temperature data = {temperatureLogData} ')


	########################## INTENT CAPTURE CODE ###########################################


	@IntentHandler('UserIntent')
	def sendUserIntentToHA(self, session: DialogSession):

		if self.randomTalk(text='dummyIntent') in session.payload['input']:
			self.endDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='dummyUtterance'),
				siteId=session.siteId,
			)
			return
		userSlot = session.slotValue('HAintent')

		self.MqttManager.publish(topic='ProjectAlice/HomeAssistant', payload=userSlot)
		self.endDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='homeAssistantSwitchDevice')
		)


	@IntentHandler('CreateIntent')
	def createIntentRequest(self, session: DialogSession):
		# test line
		# self.addIntentToHADialog(text='turn the tv volume up', session=session)

		self.continueDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='sayYesICanCaptureIntent'),
			intentFilter=['UserRandomAnswer'],
			currentDialogState='requestingToMakeAIntent',
			probabilityThreshold=0.1
		)


	def addIntentToHADialog(self, text: str, session):
		""" Here we capture the Users Intent and just store it for later use"""

		file = self.getResource(f'dialogTemplate/{self.activeLanguage()}.json')
		if not file:
			return False

		# Read the original JSON dialogTemplate file before it gets modified
		self._data = json.loads(file.read_text())

		# enumerate over existing intents to find the userintent that we are after
		for i, suggestedIntent in enumerate(self._data['intents']):
			if "userintent" in suggestedIntent['name'].lower():
				# get a list of exisiting utterances
				self._utteranceList = suggestedIntent.get('utterances', list())

				# check the utterance doesnt already exist
				if not text in self._utteranceList:
					# we need to add slot syntax to the Utterance so store needed values and move on
					self._captureUtterances = text
					self._utteranceID = i
					self.askSlotValue(session=session)
					return True
				else:
					self.say(
						siteId=session.siteId,
						text=self.randomTalk(text='utteranceExists'), )
					self.createIntentRequest(session)

		return False


	def askSlotValue(self, session):
		self.continueDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='askSlotValue'),
			intentFilter=['UserRandomAnswer'],
			currentDialogState='requestingSlotValue',
			probabilityThreshold=0.1

		)


	@IntentHandler(intent='UserRandomAnswer', requiredState='requestingSynonymValue', isProtected=True)
	@IntentHandler(intent='UserRandomAnswer', requiredState='requestingSlotValue', isProtected=True)
	@IntentHandler(intent='UserRandomAnswer', requiredState='requestingToMakeAIntent', isProtected=True)
	def listenForAvalue(self, session):
		"""Process the users spoken input"""

		triggerType = ""
		incomingValue: str = session.payload['input']

		if 'requestingToMakeAIntent' in session.currentState:
			self._captureUtterances = incomingValue
			self.addIntentToHADialog(text=self._captureUtterances, session=session)
			return
		elif 'requestingSlotValue' in session.currentState:
			triggerType = "ConfirmSlotValue"
			self._captureSlotValue = incomingValue

		elif 'requestingSynonymValue' in session.currentState:
			triggerType = "ConfirmSynonymValue"
			self._captureSynonym = incomingValue

		if not incomingValue.lower() in self._captureUtterances.lower() and not 'ConfirmSynonymValue' in triggerType:
			self.continueDialog(
				sessionId=session.sessionId,
				text=self.randomTalk(text='keywordError', replace=[self._captureUtterances]),
				intentFilter=['UserRandomAnswer'],
				currentDialogState='requestingSlotValue',
				probabilityThreshold=0.1
			)
			return

		# verify what the user's value was, to confirm alice heard it properly
		self.continueDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='repeatIncomingValue', replace=[incomingValue]),
			intentFilter=['AnswerYesOrNo'],
			currentDialogState=triggerType,

		)


	@IntentHandler(intent='AnswerYesOrNo', requiredState='requestingShouldWeAddSynonyms', isProtected=True)
	@IntentHandler(intent='AnswerYesOrNo', requiredState='ConfirmSlotValue', isProtected=True)
	@IntentHandler(intent='AnswerYesOrNo', requiredState='ConfirmIntent', isProtected=True)
	@IntentHandler(intent='AnswerYesOrNo', requiredState='ConfirmSynonymValue', isProtected=True)
	def processYesOrNoResponse(self, session):
		"""Sorts through the multiple yes or no responces and redirects accordingly"""

		# if user replies with a Yes responce do this
		if self.Commons.isYes(session):

			if 'ConfirmIntent' in session.currentState:
				self.addIntentToHADialog(session.payload['input'], session=session)

			elif 'ConfirmSlotValue' in session.currentState:
				self.addSlotValueToCapturedIntent(self._captureSlotValue, session=session)

			elif 'requestingShouldWeAddSynonyms' in session.currentState:
				self.askSynonymValue(session)

			elif 'ConfirmSynonymValue' in session.currentState:
				self.addSynonymToSlot(self._captureSynonym, session=session)

		else:
			pointer = 0
			currentState = ""
			text = ""
			if 'ConfirmIntent' in session.currentState:
				currentState = 'requestingToMakeAIntent'
				text = 'intent'
				pointer = 1
			elif 'ConfirmSlotValue' in session.currentState:
				currentState = 'requestingSlotValue'
				text = 'slot'
				pointer = 1
			elif 'ConfirmSynonymValue' in session.currentState:
				currentState = 'requestingSynonymValue'
				text = 'Synonym'
				pointer = 1

			if pointer == 1:
				self.continueDialog(
					sessionId=session.sessionId,
					text=self.randomTalk(text='userSaidNo', replace=[text]),
					intentFilter=['UserRandomAnswer'],
					currentDialogState=currentState,
					probabilityThreshold=0.1
				)

			else:
				# user is finished adding data so let's write it to file
				self.rewriteJson(session=session)


	def addSlotValueToCapturedIntent(self, text, session):
		"""Adds slot values to the JSON file"""

		for i, suggestedSlot in enumerate(self._data['slotTypes']):
			if "haintent" in suggestedSlot['name'].lower():
				# Get all the current slot values in dialogTemplate file
				slotValue = suggestedSlot.get('values', list())

				# if the slot value doesn't already exist then let's save it
				if not text in slotValue:
					# create a dictionary and append the new slot value to original list
					dictValue = {'value': text, 'synonyms': []}
					slotValue.append(dictValue)
					# save it in self._data for later usage when we need to write to file
					self._data['slotTypes'][i]['values'] = slotValue

					# Now we know theres a slot name, let's add the correct syntax to the utterance, and save it for later writing
					self._captureUtterances = self._captureUtterances.replace(self._captureSlotValue, "{" + self._captureSlotValue + ":=>HAintent}")
					self._utteranceList.append(self._captureUtterances)
					self._data['intents'][self._utteranceID]['utterances'] = self._utteranceList

					# Now lets check if the user wants to also add synonyms for that slot
					self.askToUseSynonyms(session=session, word='a')

		return False


	def addSynonymToSlot(self, text, session):
		"""Add synonyms to the current slotValue"""

		for i, haIntentSlot in enumerate(self._data['slotTypes']):
			if "haintent" in haIntentSlot['name'].lower():

				# get any current slot vales and store it in a list
				synonymItem = haIntentSlot.get('values', list())

				# let's retrieve the actual synonyms now from the various values
				for x in synonymItem:
					# Now find the right slot to use
					if self._captureSlotValue in x['value'] and not text in synonymItem:

						# retrieve current synonyms from the slot and append to a list
						self._finalsynonymList = x.get('synonyms')
						self._finalsynonymList.append(self._captureSynonym)

						x['synonyms'] = self._finalsynonymList

						# find the right index to fit the new slot
						valueIndex = next((index for (index, d) in enumerate(synonymItem) if d["value"] == self._captureSlotValue), None)
						# store the slot list until ready to write it
						self._data['slotTypes'][i]['values'][valueIndex] = x

				# Now about to ask if the user wants to add more synonyms.
				self.askToUseSynonyms(session=session, word='another')

		return False


	def askToUseSynonyms(self, session, word: str = None):

		self.continueDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='addSyn', replace=[word]),
			intentFilter=['AnswerYesOrNo'],
			currentDialogState='requestingShouldWeAddSynonyms'
		)


	def askSynonymValue(self, session):
		word = 'synonym'
		self.continueDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='addValue', replace=[word]),
			intentFilter=['UserRandomAnswer'],
			currentDialogState='requestingSynonymValue',
			probabilityThreshold=0.0
		)


	def rewriteJson(self, session):
		file = self.getResource(f'dialogTemplate/{self.activeLanguage()}.json')
		if not file:
			return False

		file.write_text(json.dumps(self._data, ensure_ascii=False, indent=4))
		self.endDialog(
			sessionId=session.sessionId,
			text=self.randomTalk(text='finishUp')
		)
