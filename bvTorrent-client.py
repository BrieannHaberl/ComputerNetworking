from sys import getsizeof
from sys import argv
from socket import *
import threading
import hashlib
import random
import time

#Listener connection for client to client
listener = socket(AF_INET, SOCK_STREAM)
listener.bind(('', 0)) #Bind to an open address
listenSocket = listener.getsockname()[1] #gets listener socket

#Check for correct args (Ip and port)
if len(argv) < 3:
    print("Usage: bvTorrent-client.py [IPADDR] [PORT]")
    exit()
ip = argv[1]
port = int(argv[2])

#Function from tracker code. Small modification with timeout
def getFullMsg(conn, msgLength):
    msg = b''    
    while len(msg) < msgLength:
        conn.settimeout(.25) #timeout after .25 seconds
        try:
            retVal = conn.recv(msgLength - len(msg))
        except:
            break
        msg += retVal
        if len(retVal) == 0:
            break
    return msg

#Function from the tracker code
def getLine(conn):
    msg = b''
    while True:
        ch = conn.recv(1)
        msg += ch
        if ch == b'\n' or len(ch) == 0:
            break
    return msg.decode()

########## Create connection ############
# takes in ip and port then makes a new connection based off those and returns it
def createConnection(ip, port):
    tmpsocket = socket(AF_INET, SOCK_STREAM)
    tmpsocket.connect((ip, port))
    return tmpsocket

########## Get Initial Server Data ############
# gets all data from server and puts them into respective variables
def getInitial():
    print("Getting initial data from server")
    serverSocket = createConnection(ip, port)
    fileName = getLine(serverSocket)
    chunkSize = getLine(serverSocket)
    chunkNum = getLine(serverSocket)
    chunkMask = "0"*int(chunkNum)+"\n"
    checkSum = []
    for i in range(0, int(chunkNum)): #Gets all checksum values
        check = getLine(serverSocket).split(',')[1]
        checkSum.append(check)
    
    sendInfo = f'{listenSocket},{chunkMask}' #sends client info to server for other clients to use
    serverSocket.send(sendInfo.encode())
    return chunkNum, chunkSize, chunkMask, serverSocket, fileName, checkSum

###### Get Client List ########3
#Takes in a server socket and returns the list of clients from server
def getClients(serverSocket):
    clientList = []
    serverSocket.send(("CLIENT_LIST\n").encode())
    numClients = getLine(serverSocket)
    for i in range(0, int(numClients)): #appends all clients to clientList
        clientList.append(getLine(serverSocket))
    return clientList

####### Updates client mask in server ########
def updateMask(chunkMask, serverSocket):
    serverSocket.send(("UPDATE_MASK\n").encode())
    serverSocket.send(chunkMask.encode())

######## Makes chunks for size of chunkNum ############
def makeChunks(chunkNum):
    chunks = [None]*int(chunkNum)
    return chunks

######### Sends chunk to other clients ###############
def sendChunk(listenerConn, chunks):
    index = getLine(listenerConn) #gets index of chunk list
    chunk = chunks[int(index)] #get chunk from index
    listenerConn.send(chunk) # sends chuhnk

############### Downloads chunk from other client ##############
def getChunk(chunkNum, chunkSize, chunkMask, chunks, clientList, serverSocket, checkSum):
    for clients in clientList: #For each client, get ip info and the chunk mask
        ipInfo, clientMask = clients.split(',')
        newList = list(range(0, int(chunkNum))) #Make a random list of numbers so then chunks are not downloaded one after another
        random.shuffle(newList)

        for i in newList: #For each number in randList
            if chunkMask[i] == "0" and clientMask[i] == "1": #If current doesnt have chunk but connected client does
                clientIp,clientPort = ipInfo.split(':') #Get connection infor
                clientSocket = createConnection(clientIp, int(clientPort)) #Connect to client
                clientSocket.send((str(i)+"\n").encode()) #Send chunk index to cleint
                chunk = getFullMsg(clientSocket, int(chunkSize)) #Get chunk from other client
                newCheck = hashlib.sha224(chunk).hexdigest() #Make new checksum
                check = (checkSum[i])[:-1]
                if check == newCheck: #check if checksums match
                    chunks[i] = chunk #if they match then update checksum and close the connection
                    tmp = list(chunkMask)
                    tmp[i] = "1"
                    chunkMask = "".join(tmp)
                    clientSocket.close()

        #Update mask after each client and get updated client list after each client. This is done this way so then it can get a new list of clients in after each client with a new chunk mask so then when it checks for a new client the lists are fully updated
        updateMask(chunkMask, serverSocket)
        clientList = getClients(serverSocket) 
    return chunkMask, chunks

#Makes file after all chunks are recieved
def makeFile(chunks, fileName):
    file = b''.join(chunks) #joins bytes
    with open(fileName[:-1], 'wb') as f:
        f.write(file)
    
#Thread function for listening
def incomingConn(chunks):
    listener.listen(8) #Chose 8 because server supports 32 connects. Needed less than 32, assumed more than 8 clients would not be connected at once and 8 is a good hex number
    while True: #Keep excepting unless interrupt
        connection, ip = listener.accept()
        sendChunk(connection, chunks)

running = True
while running: #Endless loop till keyboard interrupt
    try:
        chunkNum, chunkSize, chunkMask, serverSocket, fileName, checkSum = getInitial() #runs function for initial info
        chunks = makeChunks(chunkNum) #gets list of chunks
        threading.Thread(target = incomingConn, args = (chunks,), daemon = True).start() #Starts thread in thread function

        #while the chunkmask is not full. Get new list of clients and run getChunk
        while str(chunkMask) != ("1"*int(chunkNum) + "\n"):
            clients = getClients(serverSocket) #if reciever somehow runs out of clients before downloading all chunks
            chunkMask, chunks = getChunk(chunkNum, chunkSize, chunkMask, chunks, clients, serverSocket, checkSum)
        print("Waiting for 2 minutes to keep sending data")
        time.sleep(120) #sleep for 2 minutes to keep sending to others
        serverSocket.send(("DISCONNECT\n").encode()) #disconnect from server and make the file
        serverSocket.close()
        makeFile(chunks, fileName)
        running = False

    except KeyboardInterrupt: #disconnect if keyboard interrupt
        print("\n[Shutting Down]")
        serverSocket.send(("DISCONNECT\n").encode())
        serverSocket.close()
        running = False

