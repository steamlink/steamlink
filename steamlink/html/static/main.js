var socketNamespace = "/sl";

function Stream(sock, config, on_new_message) {

  console.log("Creating stream");

  this.config = config;
  this.record_type = {};
  this.key_field = {};
  this.start_key = {};
  this.end_key = {};
  this.cache = [];
  var self = this;
  
  this.startStream = function() {
    console.log("Starting stream with config:");
    console.log(this.config);
    sock.emit("startstream", {
      record_type: this.config.record_type,
      start_key: this.config.start_key,
      key_field: this.config.key_field,
      count: this.config.count,
      end_key: this.config.end_key,
      return_children: this.config.return_children,
      stream_tag: this.config.stream_tag,
      force: this.config.force
    }, function (data){ // on ack
      if (data.error) {
        console.log("Err: " + data.error);
      } else { // store key field and record type
        console.log("Ack rcvd");
        console.log(data);
        self.record_type = data.record_type;
        self.key_field = data.key_field;
        console.log(self);
      }
    });
  };

  this.newStreamData = function(data) {
    console.log("New websocket data");
    console.log(data);
    // back-end can ask for either an add, modify, or delete
    // first see if we have record in cache
    console.log();
    console.log("Cache is:");
    console.log(self.cache);
    var foundIndex = self.cache.findIndex(function(e){
      return e[self.key_field] === data[self.key_field];
    });
    if (_del_key in data) { // if delete:
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
          return (e[self.key_field] > data[self.key_field])
          });
        if (insertionIndex >= 0) { // if insertion index is found
          self.cache.splice(insertionIndex, 0, data);
          if (data[self.key_field] < self.start_key) {
            self.start_key = data[self.key_field];
          }
        } else { // must insert at end
          self.cache.push(data);
          self.end_key = data[self.key_field];
        }
        // TODO: cache pruning?
      }  
    }
    on_new_message(data);
  };

  sock.on(self.config.stream_tag, self.newStreamData);
}

// On Document Ready
$(function() {
  // Setup socket.io
  socket = io.connect(socketNamespace);

  socket.on("connect", function() {
    socket.emit("connected", { data: "I'm connected!" });
  });

  socket.on("disconnect", function() {
    console.log("dead");
  });
});
