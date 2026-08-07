/**
 * sheet-sink.gs — Google Apps Script that receives SMS records from the
 * listener service and appends them to a Google Sheet.
 *
 * SETUP (one time, ~2 minutes):
 *   1. Create a Google Sheet. Name the first tab "SMS Log".
 *   2. Extensions -> Apps Script. Paste this whole file in. Save.
 *   3. Deploy -> New deployment -> type "Web app".
 *        - Execute as:  Me
 *        - Who has access:  Anyone
 *      Deploy, authorize, and copy the Web app URL.
 *   4. Set that URL as SHEETS_WEBHOOK_URL in the listener service's env.
 *
 * The first write auto-creates the header row.
 */

var SHEET_NAME = 'SMS Log';
var HEADERS = [
  'receivedAt', 'direction', 'contactName', 'phone',
  'body', 'contactId', 'conversationId', 'messageId',
  'locationId', 'messageType'
];

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var sheet = getSheet_();
    var row = HEADERS.map(function (h) {
      var v = data[h];
      return v === undefined || v === null ? '' : v;
    });
    sheet.appendRow(row);
    return json_({ ok: true });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function getSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
