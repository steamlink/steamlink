var socketNamespace = "/sl";

function Stream(sock, config, on_new_message) {

  console.log("Creating stream");

  /*****

  Config search fields:
  
  table_name
  key_field
  restrict_by
  start_key
  start_item_number
  count
  end_key

  ******/

  this.config = config;

  // config
  this.cache = [];
  var self = this;
  
  this.startStream = function() {
    console.log("Starting stream with config:");
    console.log(self.config);
    sock.emit("startstream", {
      table_name: self.config.table_name,
      key_field: self.config.key_field,
      restrict_by: self.config.restrict_by,
      start_key: self.config.start_key,
      start_item_number: self.config.start_item_number,
      count: self.config.count,
      end_key: self.config.end_key,
      stream_tag: self.config.stream_tag
    }, function (data){ // on ack
      if (data.error) {
        console.log("Err: " + data.error);
      } else { // store key field and record type
        console.log("Ack rcvd");
        console.log(data);
        self.config.start_key = data.start_key;
        self.config.end_key = data.end_key;
        self.config.count = data.count;
        self.config.start_item_number = data.start_item_number;
        self.config.total_item_count = data.total_item_count;
      }
    });
  };

  // TODO: refactor
  this.updateStream = function() {
    self.cache = [];
    // self.config = newConfig;
    console.log("Updating stream with config:");
    console.log(self.config);
    sock.emit("startstream", {
      table_name: self.config.table_name,
      key_field: self.config.key_field,
      restrict_by: self.config.restrict_by,
      start_key: self.config.start_key,
      start_item_number: self.config.start_item_number,
      count: self.config.count,
      end_key: self.config.end_key,
      stream_tag: self.config.stream_tag
    }, function (data){ // on ack
      if (data.error) {
        console.log("Err: " + data.error);
      } else { // store key field and record type
        console.log("Ack rcvd");
        console.log(data);
        self.config.start_key = data.start_key;
        self.config.end_key = data.end_key;
        self.config.count = data.count;
        self.config.start_item_number = data.start_item_number;
        self.config.total_item_count = data.total_item_count;
      }
    });
  };

  this.newStreamData = function(data) {
    // back-end can ask for either an add, modify, or delete
    // first see if we have record in cache
    var foundIndex = self.cache.findIndex(function(e){
      return e[self.config.key_field] === data[self.config.key_field];
    });
    if ('_del_key' in data) { // if delete:
      if (foundIndex >= 0) { // if key exists in cache
        self.cache.splice(foundIndex, 1); 
      } else { // key doesn't exist in cache:
        console.log("debug: ignoring _del_key: " + data._del_key);
      }
    } else { // if add/modify:
      if (foundIndex >= 0) { // if key exists in cache
        // update the cached record
        self.cache[foundIndex] = data; 
      } else { // if key not in cache
        // find insertion point
        insertionIndex = self.cache.findIndex(function(e) {
          // assume cache is ordered by key_field
          return (e[self.config.key_field] > data[self.config.key_field])
          });
        if (insertionIndex >= 0) { // if insertion index is found
          self.cache.splice(insertionIndex, 0, data);
          if (data[self.config.key_field] < self.config.start_key) {
            self.config.start_key = data[self.config.key_field];
          }
        } else { // must insert at end
          self.cache.push(data);
          self.config.end_key = data[self.config.key_field];
        }
        // TODO: cache pruning?
      }  
    }
    on_new_message(data);
  };

  window.socketStreams.streams.push(this);
  sock.on(self.config.stream_tag, self.newStreamData);
}

function sendCommand(sock, command) {
  console.log(sendingCommand);
  sock.emit("cmd", command);
}

// On Document Ready
$(function() {
  // Setup socket.io
  
  window.socketStreams = {
    'streams' : [],
    'reconnect' : false
  };
  
  socket = io.connect(socketNamespace);

  var alertWrapper = $('#dashboard_socket_alerts');  
  var alertElement =  $('#dashboard_socket_alerts > .alert-msg')[0];
  var alertCloseElement = $('#dashboard_socket_alerts a')[0];

  lvlColors = {
    "EMERGENCY" : "lightred",
    "ALERT" : "lightred",
    "CRITICAL" : "lightsalmon",
    "ERROR" : "lightsalmon",
    "WARNING" : "lightyellow",
    "NOTICE" : "lightgreen",
    "INFO": "lightblue",
    "DEBUG": "lightblue"    
  }

  var renderAlert= function (msg, lvl) {
    // TODO: Map lvl to color
    console.log("socket connection dead");
    alertElement.innerHTML = lvl + ": " + msg;
    alertCloseElement.innerHTML = "[ ✕ ]"; 
    $(alertWrapper).css({"color": "black"})
    $(alertWrapper).css({"background-color": lvlColors[lvl]})
    $(alertWrapper).show();
  }

  alertCloseElement.onclick = function(){
    $(alertWrapper).hide();
  }

  socket.on("disconnect", () => {
      console.log("socket connection dead");
      msg = "Websocket disconnected...";
      lvl = "WARNING";
      renderAlert(msg, lvl);
      window.socketStreams.connected = true;
  });

  socket.on("connect", () => {
      socket.emit("connected", { data: "I'm connected!" });
      $(alertWrapper).hide();
      if (window.socketStreams.connected) {
        window.socketStreams.streams.forEach((stream) => {
          stream.startStream();
        });
      }
  });

  socket.on("alert", (data) => {
    renderAlert(data.msg, data.lvl);
  });
  
});
