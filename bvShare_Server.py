from socket import *
from pathlib import Path
from os import path

port = 11111
serverSock = socket(AF_INET, SOCK_STREAM)

serverSock.bind(('',port))
serverSock.listen()
print(f'Running on {port}')


while True:
    clientConn, clientAddr = serverSock.accept()
    p = Path('./repository/')
    repo = p.iterdir()
    files = []

    for abso in repo:
        if '/' in str(abso):
            file = str(abso).split('/')[-1]
        else:
            file = str(abso).split('\\')[-1]
        files.append(file)
    

    # Sends Client number of files in repository/
    clientConn.send(len(files).to_bytes(4,'little'))
    for f in files:
        clientConn.send(len(f).to_bytes(2,'little'))
        clientConn.send(f.encode())
    
    fId = int.from_bytes(clientConn.recv(4), 'little')
   
    # Finds the file client requested and sends the size of said file
    fileToSend = 'repository/'+files[fId - 1]
    file_size = path.getsize(fileToSend)
    clientConn.send(file_size.to_bytes(4, 'little'))
    
    # Sending file_size bytes
    with open(fileToSend, 'rb') as f:
        clientConn.send(f.read(file_size))

    clientConn.close()


print('Closing')
serverSock.close()
