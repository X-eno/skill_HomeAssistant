import sqlite3

from core.device.model.Device import Device
from pathlib import Path
from core.device.model.DeviceAbility import DeviceAbility


class HAsensor(Device):

	@classmethod
	def getDeviceTypeDefinition(cls) -> dict:
		return {
			'deviceTypeName'        : 'HAsensor',
			'perLocationLimit'      : 0,
			'totalDeviceLimit'      : 0,
			'allowLocationLinks'    : True,
			'allowHeartbeatOverride': True,
			'heartbeatRate'         : 320,
			'abilities'             : [DeviceAbility.NONE]
		}


	def __init__(self, data: sqlite3.Row):
		self._imagePath = f'{self.Commons.rootDir()}/skills/HomeAssistant/devices/img/'
		super().__init__(data)


	def getDeviceIcon(self) -> Path:
		if self.connected:
			return Path(f'{self._imagePath}GeneralSensors/HAsensor.png')

		return Path(f'{self._imagePath}GeneralSensors/HAsensorOffline.png')


	def onUIClick(self):

		answer = f"The {self.displayName} currently reads {self.getParam('state')}"
		self.MqttManager.say(text=answer)
		return super().onUIClick()
