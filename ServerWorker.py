from random import randint
import sys, traceback, threading, socket

from VideoStream import VideoStream
from RtpPacket import RtpPacket
import time

MAX_PAYLOAD_SIZE = 1400  
JPEG_HEADER_SIZE = 8     

class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2
	
	clientInfo = {}
	
	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		
	def run(self):
		threading.Thread(target=self.recvRtspRequest).start()
	
	def recvRtspRequest(self):
		connSocket = self.clientInfo['rtspSocket'][0]
		while True:            
			data = connSocket.recv(256)
			if data:
				print("Data received:\n" + data.decode("utf-8"))
				self.processRtspRequest(data.decode("utf-8"))
	
	def processRtspRequest(self, data):
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		filename = line1[1]
		seq = request[1].split(' ')
		
		if requestType == self.SETUP:
			if self.state == self.INIT:
				print("processing SETUP\n")
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				
				self.clientInfo['session'] = randint(100000, 999999)
				self.replyRtsp(self.OK_200, seq[1])
				self.clientInfo['rtpPort'] = request[2].split(' ')[3]
		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				self.replyRtsp(self.OK_200, seq[1])
				
				self.clientInfo['event'] = threading.Event()
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
				self.clientInfo['worker'].start()
		
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				self.clientInfo['event'].set()
				self.replyRtsp(self.OK_200, seq[1])
		
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")
			self.clientInfo['event'].set()
			self.replyRtsp(self.OK_200, seq[1])
			self.clientInfo['rtpSocket'].close()
			
	def sendRtp(self):
		FPS = 24
		FRAME_PERIOD = 1.0 / FPS
		MAX_FRAGMENT_SIZE = MAX_PAYLOAD_SIZE - JPEG_HEADER_SIZE

		next_frame_time = time.time()

		while True:
			now = time.time()
			time_to_sleep = next_frame_time - now
			
			if time_to_sleep > 0:
				self.clientInfo['event'].wait(time_to_sleep)

			if self.clientInfo['event'].isSet(): 
				break
				
			data = self.clientInfo['videoStream'].nextFrame()
			if data: 
				frameNumber = self.clientInfo['videoStream'].frameNbr()
				try:
					address = self.clientInfo['rtspSocket'][1][0]
					port = int(self.clientInfo['rtpPort'])

					total_size = len(data)
					offset = 0
					total_size_bytes = total_size.to_bytes(4, byteorder='big')

					while offset < total_size:
						chunk_size = min(total_size - offset, MAX_FRAGMENT_SIZE)
						fragment_data = data[offset : offset + chunk_size]

						offset_bytes = offset.to_bytes(4, byteorder='big')
						jpeg_header = offset_bytes + total_size_bytes
						payload = jpeg_header + fragment_data

						offset += chunk_size
						marker = 1 if offset == total_size else 0

						rtp_packet = self.makeRtp(payload, frameNumber, marker)
						self.clientInfo['rtpSocket'].sendto(rtp_packet, (address, port))
				except:
					print("Connection Error")
     
			next_frame_time += FRAME_PERIOD

	def makeRtp(self, payload, frameNbr, marker):
		version = 2
		padding = 0
		extension = 0
		cc = 0
		pt = 26 
		seqnum = frameNbr
		ssrc = 0 
		
		rtpPacket = RtpPacket()
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
		
		return rtpPacket.getPacket()
		
	def replyRtsp(self, code, seq):
		if code == self.OK_200:
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")