# -*- coding: utf-8 -*-

# Copyright 2015 Christian Wiwie <derwiwie@googlemail.com>

import glob
import gtk
import logging
import pango
import gobject

from Tkinter import *
from tkintertable.Tables import *
from tkintertable.TableModels import *

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
		lang = InsertTableDialog(self.window, self.window.pageview).run()
		if not lang:
			return # dialog cancelled
		else:
			obj = TableObject({'type': OBJECT_TYPE}, '', self.window.ui) # XXX
			pageview = self.window.pageview
			pageview.insert_object(pageview.view.get_buffer(), obj)

class InsertTableDialog(Dialog):

	object_type = 'table'
	scriptname = 'table.tex'
	imagename = 'table.png'

	def __init__(self, ui, pageview, table=None):
		if table is None:
			Dialog.__init__(self, ui, _('Insert Table'), # T: Dialog title
				button=(_('_Insert'), 'gtk-ok'),  # T: Button label
				defaultwindowsize=(350, 400) )
		else:
			Dialog.__init__(self, ui, _('Edit Table'), # T: Dialog title
				button=(_('_Edit'), 'gtk-ok'),  # T: Button label
				defaultwindowsize=(350, 400) )
		self.table = table
		self.pageview = pageview
		
		self.rownames = gtk.ListStore(int, gobject.TYPE_STRING)
		self.columnNames = gtk.ListStore(int, str)

		self.template = get_template('plugins', 'equationeditor.tex')
		self.texfile = TmpFile(self.scriptname)
		
		self.rowIds = [1,2]
		self.colIds = [1,2]
		if table is not None:
			self.edit = True
			self.init_table(table=self.table)
		else:
			self.edit = False
			self.init_table()
			
	# if no new columns are specified, we just return a copy of the old store
	def add_columns_to_store(self, storeOld, newColumnTypes=[]):
		rowNames = gtk.ListStore(str)
		columnNames = gtk.ListStore(str)
		# copy columns
		columnTypes = []
		for c in range(0,storeOld.get_n_columns()):
			columnTypes.append(storeOld.get_column_type(c))
			
		columnTypes.extend(newColumnTypes)
		store = gtk.ListStore(*columnTypes)
		# copy rows
		r = 0
		for row in storeOld:
			newRow = []
			for value in row:
				newRow.append(value)
			for newColumn in newColumnTypes:
				newRow.append('')
			store.append(newRow)
			if r > 0:
				rowNames.append([newRow[0]])
			else:
				for p in range(1,len(row)):
					columnNames.append([newRow[p]])
				for newColumn in newColumnTypes:
					columnNames.append([''])
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
			
	def init_table(self, table=None):
		import gobject
		
		treeviewOld = table.treeview
		storeOld = treeviewOld.get_model()
		# copy model
		store, rownames, columnames = self.add_columns_to_store(storeOld)
		
		self.set_store(store)
		ncol = store.get_n_columns()
		self.treeview = gtk.TreeView(store)
		self.treeview.set_headers_visible(False)
			
		c = 0
		for columnOld in treeviewOld.get_columns():
			column = gtk.TreeViewColumn()
			cellrender = gtk.CellRendererText()
			cellrender.set_property('editable', True)
			cellrender.connect('edited', self.edited_cb, (store, c))
			column.pack_start(cellrender, True)
			column.add_attribute(cellrender, 'text', c)
			self.treeview.append_column(column)
			c = c + 1
		
		table = gtk.Table(rows=2,columns=2,homogeneous=False)
		table.attach(self.treeview,left_attach=0, right_attach=1, top_attach=0, bottom_attach=1)
		addRowBtn = gtk.Button("+")
		addRowBtn.connect('clicked', self.on_add_row)
		table.attach(addRowBtn,left_attach=0, right_attach=1, top_attach=1, bottom_attach=2)
		addColBtn = gtk.Button("+")
		addColBtn.connect('clicked', self.on_add_col)
		table.attach(addColBtn,left_attach=1, right_attach=2, top_attach=0, bottom_attach=1)
		self.vbox.add(table)
		self.treeview.show_all()
		
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
		
		self.vbox.add(table)
		
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
		liststore[path][column] = new_text
		if column == 0:
			self.update_row_names()
		if path == 0:
			self.update_col_names()
		return
		
	def on_rowheader_insert(self, textview, text):
		print(gtk.gdk.keyval_name(text.keyval))
		if text.keyval == gtk.keysyms.Tab or text.keyval == gtk.keysyms.ISO_Left_Tab:
			textview.emit_stop_by_name('key-press-event')
		
	def on_add_row(self, button):
		newRow = []
		for c in range(0, self.treeview.get_model().get_n_columns()):
			newRow.append('')
		self.treeview.get_model().append(newRow)
		self.update_row_names()
		
	def on_del_row(self, button):
		active_row = self.remove_row_combo.get_active_iter()
		if active_row is None:
			return
		
		# search for that row in the data store
		row_in_model = self.remove_row_combo.get_model().get_path(active_row)
		target_iter = self.treeview.get_model().get_iter(row_in_model)
		# we have to go to next row, since we have an additional header row in the data store
		target_iter = self.treeview.get_model().iter_next(target_iter)
		self.treeview.get_model().remove(target_iter)
		
	def on_add_col(self, button):
		c = self.treeview.get_model().get_n_columns()
		store, rownames, columnames = self.add_columns_to_store(self.treeview.get_model(), newColumnTypes=[gobject.TYPE_STRING])
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
		
		
	def on_del_col(self, button):
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
		
		self.treeview.remove_column(self.treeview.get_column(target_col))

	def do_response_ok(self):
		self.table.treeview.set_model(self.treeview.get_model())
		# TODO: adapt treeview to new columns
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
		Dialog.run(self)
		


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
			print(text)
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
		lang = InsertTableDialog(win, pageview, table=self).run()
		self.set_modified(True)
		if not lang:
			return # dialog cancelled
		else:
			return
