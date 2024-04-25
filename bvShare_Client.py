#!/usr/bin/env python3

from sys import argv
from os import path
from pathlib import Path

from socket import *



serverIP = argv[1]
serverPort = int(argv[2])


#Connect to server
clientSocket = socket(AF_INET, SOCK_STREAM)
clientSocket.connect( (serverIP, serverPort) )

#receive the number of files that are in the repository
msgRec = clientSocket.recv(4)
numFiles = int.from_bytes(msgRec,'little')

fileNames = []

#receive each file name and display them with a ID
count = 1
for i in range(numFiles):
    msgR = clientSocket.recv(2)
    nameLen = int.from_bytes(msgR, 'little')

    fileName = clientSocket.recv(nameLen).decode()

    fileNames.append(fileName)

    print(f"[{count}] {fileName}")
    count += 1


msgID = int(input("What file do you want: "))

#Store the file name
finalFileName = fileNames[msgID -1]

#Check to see if the user put 0
if msgID == 0:
    print("invalid ID")
    exit()


#Convert the id to bytes
idBytes = msgID.to_bytes(2, 'little')


clientSocket.send( idBytes )


#Receive file size
fileSizebytes = clientSocket.recv(4)
fileSize = int.from_bytes(fileSizebytes, 'little')

#Receive bytes until we get the entire file size
bytesRecv = 0
msg = b'' 
while bytesRecv != fileSize:
    msg += clientSocket.recv(fileSize - bytesRecv)
    bytesRecv = len(msg)

#Write the file
with open("repository/" + finalFileName, "wb") as f:
    f.write(msg)

