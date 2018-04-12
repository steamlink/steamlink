var socketNamespace = "/sl";

// helper function
function joinRoom(skt, room) {
  console.log("Joining room: ", room);
  skt.emit("join", { room: room });
}
function leaveRoom(skt, room) {
  console.log("Leaving room: ", room);
  skt.emit("leave", { room: room });
}

// On Document Ready
$(function() {
  // Setup socket.io
  socket = io.connect(socketNamespace);

  socket.on("connect", function() {
    socket.emit("connected", { data: "I'm connected!" });
    joinRoom(socket, "meshes");
  });

  socket.on("disconnect", function() {
    console.log("dead");
  });

  socket.on("data_full", function(msg) {
    console.log("Received data!");
    console.log(msg);
    if (msg.header) {
      console.log("updating header message");
      // TODO: do something
    } else {
      console.log("updating tile message");
      // TODO: do something
    }
  });

});
