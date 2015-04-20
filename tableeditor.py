# -*- coding: utf-8 -*-

# Copyright 2015 Christian Wiwie <derwiwie@googlemail.com>

import glob
import gtk
import logging
import pango
import gobject

from zim.plugins import PluginClass, extends, WindowExtension
from zim.actions import action
from zim.gui.widgets import Dialog, Button, InputEntry, ScrolledWindow
from zim.plugins.base.imagegenerator import ImageGeneratorPlugin, ImageGeneratorClass
from zim.fs import File, TmpFile
from zim.config import data_file
from zim.templates import get_template
from zim.applications import Application, ApplicationError
from zim.objectmanager import ObjectManager, CustomObjectClass
from zim.gui.pageview import CustomObjectBin, POSITION_BEGIN, POSITION_END

logger = logging.getLogger('zim.plugins.inserttable')

OBJECT_TYPE = 'table'

class TableEditorPlugin(PluginClass):

	plugin_info = {
		'name': _('Insert Table'), # T: plugin name
		'description': _('''\
This plugin adds the 'Insert Table' dialog and allows
auto-formatting typographic characters.

This is a core plugin shipping with zim.
'''), # T: plugin description
		'author': 'Christian Wiwie',
		'help': 'Plugins:Insert Table',
		'object_types': (OBJECT_TYPE, ),
	}
	
	@classmethod
	def check_dependencies(klass):
		return True, []

	def __init__(self, config=None):
		PluginClass.__init__(self, config)

	def create_object(self, attrib, text, ui=None):
		'''Factory method for TableObject objects'''
		obj = TableObject(attrib, text, ui) # XXX
		return obj


@extends('MainWindow')
class MainWindowExtension(WindowExtension):

	uimanager_xml = '''
	<ui>
	<menubar name='menubar'>
		<menu action='insert_menu'>
			<placeholder name='plugin_items'>
				<menuitem action='insert_table'/>
			</placeholder>
		</menu>
	</menubar>
	</ui>
	'''

	def __init__(self, plugin, window):
		WindowExtension.__init__(self, plugin, window)
		ObjectManager.register_object(OBJECT_TYPE, self.plugin.create_object)

	def teardown(self):
		ObjectManager.unregister_object(OBJECT_TYPE)

	@action(_('Ta_ble...')) # T: menu item
	def insert_table(self):
		'''Run the InsertTableDialog'''
		obj = TableObject({'type': OBJECT_TYPE}, '|Column 1|Column 2|\nRow 1|||\nRow 2|||\n', self.window.ui) # XXX
		dialog = InsertTableDialog(self.window, self.window.pageview, table=obj, edit=False)
		lang = dialog.run()
		if not lang:
			return # dialog cancelled
		else:
			pageview = self.window.pageview
			pageview.insert_object(pageview.view.get_buffer(), obj)

class InsertTableDialog(Dialog):

	object_type = 'table'
	scriptname = 'table.tex'
	imagename = 'table.png'

	def __init__(self, ui, pageview, table=None, edit=False):
		if not edit:
			Dialog.__init__(self, ui, _('Insert Table'), # T: Dialog title
				button=(_('_Insert'), 'gtk-ok'),  # T: Button label
				buttons=gtk.BUTTONS_OK_CANCEL)
		else:
			Dialog.__init__(self, ui, _('Edit Table'), # T: Dialog title
				button=(_('_Edit'), 'gtk-ok'),  # T: Button label
				buttons=gtk.BUTTONS_OK_CANCEL)
		self.edit = edit
		self.table = table
		self.pageview = pageview
		
		self.rownames = gtk.ListStore(int, gobject.TYPE_STRING)
		self.columnNames = gtk.ListStore(int, str)

		self.template = get_template('plugins', 'equationeditor.tex')
		self.texfile = TmpFile(self.scriptname)
		
		self.rowIds = [1,2]
		self.colIds = [1,2]
		if table is not None:
			self.init_table(table=self.table)
		else:
			self.init_table()
			
	# if no new columns are specified, we just return a copy of the old store
	def add_columns_to_store(self, storeOld, newColumnTypes=[]):
		rowNames = gtk.ListStore(str)
		columnNames = gtk.ListStore(str)
		# copy columns
		columnTypes = []
		for c in range(0,storeOld.get_n_columns()):
			columnTypes.append(storeOld.get_column_type(c))
			
		#columnTypes.extend(newColumnTypes)
		for newColumnType in newColumnTypes:
			columnTypes.insert(newColumnType[1], newColumnType[0])
		
		store = gtk.ListStore(*columnTypes)
		# copy rows
		r = 0
		for row in storeOld:
			newRow = []
			for value in row:
				newRow.append(value)
			for newColumn in newColumnTypes:
				newRow.insert(newColumn[1], '')
			store.append(newRow)
			if r > 0:
				rowNames.append([newRow[0]])
			else:
				for p in range(1,len(row)):
					columnNames.append([newRow[p]])
				for newColumn in newColumnTypes:
					columnNames.insert(newColumn[1], [''])
			r = r + 1
		self.set_store(store)
		return store, rowNames, columnNames
	
	def set_store(self, newStore):
		self.store = newStore
		
		# update combo boxes initially
		self.update_row_names()
		self.update_col_names()
		
		self.store.connect('row-changed',self.on_row_changed)
		self.store.connect('row-deleted',self.on_row_deleted)
		self.store.connect('row-inserted',self.on_row_inserted)
		
	def set_treeview(self, treeview):
		self.treeview = treeview
		
		self.treeview.connect('button-press-event', self.on_button_pressed)
		
	def on_button_pressed(self, treeview, event):
		if event.button == 3:
			x = int(event.x)
			y = int(event.y)
			pthinfo = treeview.get_path_at_pos(x, y)
			if pthinfo is not None:
				time = event.time
				path, col, cellx, celly = pthinfo
				treeview.grab_focus()
				treeview.set_cursor( path, col, 0)
				
				self.popup = None
				# show menu entry to delete column
				# check that we clicked row 0 and that column is not the first (non-editable) column
				colInd = -1
				c = 0
				for column in treeview.get_columns():
					if column == col:
						colInd = c
					c = c + 1
				if path[0] == 0 and colInd > 0:
					self.popup = gtk.Menu()
					win = treeview.get_parent_window()
							
					item = gtk.MenuItem(_('Delete Column \'' + self.columnNames[colInd-1][1] + '\''))
					item.connect_after('activate', 
						lambda o: self.on_del_col(colInd-1))
					self.popup.prepend(item)
					
					item = gtk.MenuItem(_('Insert Column Left Of \'' + self.columnNames[colInd-1][1] + '\''))
					item.connect_after('activate', 
						lambda o: self.on_add_col(colInd))
					self.popup.prepend(item)
					
					item = gtk.MenuItem(_('Insert Column Right Of \'' + self.columnNames[colInd-1][1] + '\''))
					item.connect_after('activate', 
						lambda o: self.on_add_col(colInd+1))
					self.popup.prepend(item)

				if path[0] > 0 and colInd == 0:
					self.popup = gtk.Menu()
					win = treeview.get_parent_window()
							
					item = gtk.MenuItem(_('Delete Row \'' + self.rownames[path[0]-1][1] + '\''))
					item.connect_after('activate', 
						lambda o: self.on_del_row(path[0]-1))
					self.popup.prepend(item)
					
					item = gtk.MenuItem(_('Insert Row Above Of \'' + self.rownames[path[0]-1][1] + '\''))
					item.connect_after('activate', 
						lambda o: self.on_add_row(path[0]))
					self.popup.prepend(item)
					
					item = gtk.MenuItem(_('Insert Row Below Of \'' + self.rownames[path[0]-1][1] + '\''))
					item.connect_after('activate', 
						lambda o: self.on_add_row(path[0]+1))
					self.popup.prepend(item)
					
				print(self.popup)
				
				if self.popup is not None:
					self.popup.show_all()
					self.popup.popup( None, None, None, event.button, time)
			return True
			
	def init_table(self, table=None):
		import gobject
		
		if table is None:
			storeOld = gtk.ListStore(str, str, str)
			storeOld.append(['','Column 1','Column 2'])
			storeOld.append(['Row 1','',''])
			storeOld.append(['Row 2','',''])
		else:
			treeviewOld = table.treeview
			storeOld = treeviewOld.get_model()
		# copy model
		store, rownames, columnames = self.add_columns_to_store(storeOld)
		
		self.set_store(store)
		ncol = store.get_n_columns()
		treeview = gtk.TreeView(store)
		treeview.set_headers_visible(False)
			
		for c in range(0,storeOld.get_n_columns()):
			column = gtk.TreeViewColumn()
			cellrender = gtk.CellRendererText()
			cellrender.set_property('editable', True)
			cellrender.connect('edited', self.edited_cb, (store, c))
			column.pack_start(cellrender, True)
			column.add_attribute(cellrender, 'text', c)
			treeview.append_column(column)
		
		table = gtk.Table(rows=2,columns=2,homogeneous=False)
		table.attach(treeview,left_attach=0, right_attach=1, top_attach=0, bottom_attach=1)
		addRowBtn = gtk.Button("+")
		addRowBtn.connect('clicked', self.on_add_row)
		table.attach(addRowBtn,left_attach=0, right_attach=1, top_attach=1, bottom_attach=2)
		addColBtn = gtk.Button("+")
		addColBtn.connect('clicked', self.on_add_col)
		table.attach(addColBtn,left_attach=1, right_attach=2, top_attach=0, bottom_attach=1)
		self.vbox.pack_start(table)
		treeview.show_all()
		
		self.set_treeview(treeview)
		
		# buttons / comboboxes for removal of rows/columns
		table = gtk.Table(rows=2,columns=3,homogeneous=False)
		remove_row_btn = gtk.Button("Remove Row")
		remove_row_btn.connect('clicked', self.on_del_row)
		table.attach(remove_row_btn,left_attach=0, right_attach=1, top_attach=1, bottom_attach=2)
		
		combo = gtk.ComboBox(self.rownames)
		cell = gtk.CellRendererText()
		combo.pack_start(cell, True)
		combo.add_attribute(cell, 'text', 1)
		self.remove_row_combo = combo
		table.attach(combo,left_attach=1, right_attach=2, top_attach=1, bottom_attach=2)
		
		remove_col_btn = gtk.Button("Remove Column")
		remove_col_btn.connect('clicked', self.on_del_col)
		table.attach(remove_col_btn,left_attach=0, right_attach=1, top_attach=3, bottom_attach=4)
		
		combo = gtk.ComboBox(self.columnNames)
		cell = gtk.CellRendererText()
		combo.pack_start(cell, True)
		combo.add_attribute(cell, 'text', 1)
		self.remove_col_combo = combo
		table.attach(combo,left_attach=1, right_attach=2, top_attach=3, bottom_attach=4)
		self.tableView = table
		
		# do not add those components - have been replaced by right click menus on columns / rows
		# TODO: remove those components
		#self.vbox.add(table)
		
	def on_row_changed(self, treemodel, path, iter):
		self.update_row_names()
		self.update_col_names()
		
	def on_row_deleted(self, treemodel, path):
		self.update_row_names()
		
	def on_row_inserted(self, treemodel, path, iter):
		self.update_row_names()
	
	def update_row_names(self):
		self.rownames.clear()
		
		r = 0
		for row in self.store:
			if r > 0:
				self.rownames.append([r, row[0]])
			r = r + 1
		
	def update_col_names(self):
		self.columnNames.clear()
		
		for row in self.store:
			for p in range(1,len(row)):
				self.columnNames.append([p, row[p]])
			return
		
	def edited_cb(self, cell, path, new_text, user_data):
		liststore, column = user_data
		if column == 0 and path == '0':
			return
		self.treeview.get_model()[path][column] = new_text
		if column == 0:
			self.update_row_names()
		if path == '0':
			self.update_col_names()
		return
		
	def on_rowheader_insert(self, textview, text):
		print(gtk.gdk.keyval_name(text.keyval))
		if text.keyval == gtk.keysyms.Tab or text.keyval == gtk.keysyms.ISO_Left_Tab:
			textview.emit_stop_by_name('key-press-event')
		
	def on_add_row(self, rowInd):
		newRow = []
		r = len(self.treeview.get_model())
		if not isinstance(rowInd, ( int, long ) ):
			rowInd = r
			
		for c in range(0, self.treeview.get_model().get_n_columns()):
			newRow.append('')
		self.treeview.get_model().insert(rowInd, newRow)
		self.update_row_names()
		
	def on_del_row(self, rowInd):
		if rowInd is not None:
			self.remove_row_combo.set_active(rowInd)
		active_row = self.remove_row_combo.get_active_iter()
		if active_row is None:
			return
		
		# search for that row in the data store
		row_in_model = self.remove_row_combo.get_model().get_path(active_row)
		target_iter = self.treeview.get_model().get_iter(row_in_model)
		# we have to go to next row, since we have an additional header row in the data store
		target_iter = self.treeview.get_model().iter_next(target_iter)
		self.treeview.get_model().remove(target_iter)
		
	def on_add_col(self, colInd):
		c = self.treeview.get_model().get_n_columns()
		if not isinstance(colInd, ( int, long ) ):
			colInd = c
		
		store, rownames, columnames = self.add_columns_to_store(self.treeview.get_model(), newColumnTypes=[(gobject.TYPE_STRING, colInd)])
		self.treeview.set_model(store)
		self.set_store(store)
		
		# add column to treeview		
		column = gtk.TreeViewColumn()
		cellrender = gtk.CellRendererText()
		cellrender.set_property('editable', True)
		cellrender.connect('edited', self.edited_cb, (store, c))
		column.pack_start(cellrender, True)
		column.add_attribute(cellrender, 'text', c)
		self.treeview.append_column(column)
		
		
	def on_del_col(self, colInd):
		if colInd is not None:
			self.remove_col_combo.set_active(colInd)
		active_col = self.remove_col_combo.get_active_iter()
		if active_col is None:
			return
			
		target_col = self.remove_col_combo.get_model()[active_col][0]
		
		# create a new liststore containing all rows without the corresponding column
		columnTypes = []
		for c in range(0, self.treeview.get_model().get_n_columns()):
			if c <> target_col:
				columnTypes.append(self.treeview.get_model().get_column_type(c))
			
		newStore = gtk.ListStore(*columnTypes)
		
		for row in self.treeview.get_model():
			newRow = []
			for c in range(0, len(row)):
				if c <> target_col:
					newRow.append(row[c])
			newStore.append(newRow)
		
		self.treeview.set_model(newStore)
		self.set_store(newStore)
		# remove last column
		self.treeview.remove_column(self.treeview.get_column(self.treeview.get_model().get_n_columns()))
		
	def do_response_ok(self):
		self.table.treeview.set_model(self.treeview.get_model())
		# adapt treeview to new columns
		for column in self.table.treeview.get_columns():
			self.table.treeview.remove_column(column)
		for c in range(0,self.treeview.get_model().get_n_columns()):
			column = gtk.TreeViewColumn()
			cellrender = gtk.CellRendererText()
			column.pack_start(cellrender, True)
			column.add_attribute(cellrender, 'text', c)
			self.table.treeview.append_column(column)
		self.result = 1
		return True
		
	def cleanup(self):
		path = self.texfile.path
		for path in glob.glob(path[:-4]+'.*'):
			File(path).remove()
		
	def run(self):
		return Dialog.run(self)
		


class TableObject(CustomObjectClass):

	def __init__(self, attrib, data, ui=None):
		if data.endswith('\n'):
			data = data[:-1]
			# If we have trailing \n it looks like an extra empty line
			# in the buffer, so we default remove one
		CustomObjectClass.__init__(self, attrib, data, ui)
		self.data = None
		self.rowheader = None
		self.columnheader = None
		#self.connect('modified-changed', self.dump)
		
		self._init_widget()

	def get_widget(self):
		if not self._widget:
			self._init_widget()
		return self._widget
		
	def get_data(self):
		'''Returns data as text.'''
		if self._widget:
			text = ""
			for row in self.treeview.get_model():
				for value in row:
					text += value + "|"
				text += "\n"
			return text
		return self._data

	def _init_widget(self):
		import gobject

		box = gtk.VBox()
		
		rows = self._data.split("\n")
		# count number of columns
		nrow = len(rows)
		ncol = rows[0].count("|")

		self.rowheader = []
		self.columnheader = []
		self.data = []
		
		columnTypes = []
		for c in range(0,ncol):
			columnTypes.append(gobject.TYPE_STRING)
		store = gtk.ListStore(*columnTypes)
		
		for r in range(0,nrow):
			values = rows[r].split("|")
			
			store.append(values[0:ncol])
			
		treeview = gtk.TreeView(store)
		treeview.set_headers_visible(False)
		for c in range(0,ncol):
			column = gtk.TreeViewColumn()
			cellrender = gtk.CellRendererText()
			column.pack_start(cellrender, True)
			column.add_attribute(cellrender, 'text', c)
			treeview.append_column(column)
		
		treeview.connect('button-press-event', self.on_button_press)
		box.pack_start(treeview)
		
		self.treeview = treeview
		
		self._widget = CustomObjectBin()
		self._widget.add(box)
		
	def on_button_press(self, treeview, event):
		if event.button == 3:			
			time = event.time
			x = int(event.x)
			y = int(event.y)
			pthinfo = treeview.get_path_at_pos(x, y)
			if pthinfo is not None:
				path, col, cellx, celly = pthinfo
				treeview.grab_focus()
				treeview.set_cursor( path, col, 0)

				pageview = treeview.get_parent().get_parent().get_parent().get_parent().get_parent()
				win = treeview.get_parent_window()
						
				self.popup = gtk.Menu()
				item = gtk.MenuItem(_('Edit'))
				item.connect_after('activate', 
					lambda o: self.edit_table(pageview, win))
				self.popup.prepend(item)
				self.popup.show_all()
				
				self.popup.popup( None, None, None, event.button, time)
			return True
			
	def edit_table(self, pageview, win):
		'''Run the InsertTableDialog'''
		lang = InsertTableDialog(win, pageview, table=self, edit=True).run()
		self.set_modified(True)
		if not lang:
			return # dialog cancelled
		else:
			return
