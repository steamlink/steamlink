var socketNamespace = "/sl";

function Stream(sock, config, on_new_message) {

  console.log("Creating stream");

  this.config = config;
  this.record_type = {};
  this.key_field = {};
  this.start_key = {};
  this.end_key = {};
  this.cache = [];

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
    }, function(data){ // on ack
      if (data.error) {
        console.log("Err: " + data.error);
      } else { // store key field and record type
        console.log("Ack rcvd");
        console.log(data);
        this.record_type = data.record_type;
        this.key_field = data.key_field;
      }
    });
  };

  this.newStreamData = function(data) {
    console.log("New websocket data");
    console.log(data);
    // back-end can ask for either an add, modify, or delete
    // first see if we have record in cache
    console.log("Cache is:");
    console.log(this.cache);
    var foundIndex = this.cache.findIndex(function(e){
      return e[this.key_field] === data[this.key_field];
    });
    if (_del_key in data) { // if delete:
      if (foundIndex >= 0) { // if key exists in cache
        this.cache.splice(foundIndex, 1); 
      } else { // key doesn't exist in cache:
        console.log("debug: ignoring _del_key: " + data._del_key);
      }
    } else { // if add/modify:
      if (foundIndex >= 0) { // if key exists in cache
        // update the cached record
        this.cache[foundIndex] = data; 
      } else { // if key not in cache
        // find insertion point
        insertionIndex = this.cache.findIndex(function(e) {
          // assume cache is ordered by key_field
          return (e[this.key_field] > data[this.key_field])
          });
        if (insertionIndex >= 0) { // if insertion index is found
          this.cache.splice(insertionIndex, 0, data);
          if (data[this.key_field] < this.start_key) {
            this.start_key = data[this.key_field];
          }
        } else { // must insert at end
          this.cache.push(data);
          this.end_key = data[this.key_field];
        }
        // TODO: cache pruning?
      }  
    }
    on_new_message(data);
  }
  sock.on(this.config.stream_tag, this.newStreamData);
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
