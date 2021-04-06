import param
from bokeh.models import ColumnDataSource
import numpy as np
from pyhdx.models import Protein
import pandas as pd
from lumen.util import get_dataframe_schema

#todo refactor module to models?

from lumen.sources import Source, cached_schema


class DataFrameSource(Source):

    tables = param.Dict({}, doc="Dictionary of tables in this Source")

    updated = param.Event()


    # def __init__(self, **params):
    #     pass
        # super().__init__(**params)
        # if self.df.columns.nlevels == 1:
        #     self.tables = [self.name]
        #     self.multiindex = False
        # elif self.df.columns.nlevels == 2:
        #     self.multiindex = True
        #     self.tables = [] # todo populate tables for multiindex
        # else:
        #     raise ValueError("Currently column multiindex beyond two levels is not supported")


    def add_df(self, df, table, name):
        """
        #Todo method for adding a table to multindex source

        Parameters
        ----------
        df

        Returns
        -------

        """

        target_df = self.tables[table]
        new_index = pd.MultiIndex.from_product([[name], df.columns], names=target_df.columns.names)
        df.columns = new_index

        new_df = pd.concat([target_df, df], axis=1)

        self.tables[table] = new_df

        #todo check for clashes between higher level and lower level column names
        # pd.concat([df1, df4.reindex(df1.index)], axis=1)

        self.updated = True

    def get(self, table, **query):
        df = self.tables[table]

        # This means querying a field with the same name as higher-levels columns is not possible
        while df.columns.nlevels > 1:
            selected_col = query.pop(df.columns.names[0], False)
            if selected_col:
                df = df[selected_col]
                # df = df.dropna(how='all')  These subsets will have padded NaN rows. Remove?
            else:
                break

        dask = query.pop('__dask', False)
        df = self._filter_dataframe(df, **query)

        return df

    @cached_schema
    def get_schema(self, table=None):
        schemas = {}
        for name in self.tables:
            if table is not None and name != table:
                continue
            df = self.get(name)
            schemas[name] = get_dataframe_schema(df)['items']['properties']
        return schemas if table is None else schemas[table]

    def get_unique(self, table=None, field=None):
        """Get unique values for specified tables and fields"""
        unique_values = {}
        for name in self.tables:
            if table is not None and name != table:
                continue
            df = self.get(name)
            if field is not None:
                unique_values[name] = df[field].unique()
            else:
                unique_values[name] = {field_name: df[field_name].unique() for field_name in df.columns}

            return unique_values if table is None else unique_values[table]


class DataSource(param.Parameterized):
    tags = param.List(doc='List of tags to specify the type of data in the dataobject.')
    source = param.ClassSelector(ColumnDataSource, doc='ColumnDataSource object which is used for graphical display')
    renderer = param.String(default='line')
    default_color = param.Color(default='#0611d4')  # todo get default color from css?

    def __init__(self, input_data, **params):
        #update to lumen / pandas dataframes
        self.render_kwargs = {k: params.pop(k) for k in list(params.keys()) if k not in self.param}
        #todo currently this override colors in dic
        super(DataSource, self).__init__(**params)
        dic = self.get_dic(input_data)
        default_color = 'color' if 'color' in dic else self.default_color
        self.render_kwargs['color'] = self.render_kwargs.get('color', default_color)

        self.source = ColumnDataSource(dic, name=self.name)

    def __getitem__(self, item):
        return self.source.data.__getitem__(item)

    @property  #cached property?
    def df(self):
        df = pd.DataFrame(self.source.data)
        return df

    @property
    def export_df(self):
        df = pd.DataFrame({k: v for k, v in self.source.data.items() if not k.startswith('__')})
        return df

    def get_dic(self, input_data):
        #todo allow dataframes
        if isinstance(input_data, np.ndarray):
            dic = {name: input_data[name] for name in input_data.dtype.names}
            #self.array = input_data  #
        elif isinstance(input_data, dict):
            if 'image' in self.tags:   # Images requires lists of arrays rather than arrays
                dic = {k: v for k, v in input_data.items()}
            else:
                dic = {k: np.array(v) for k, v in input_data.items()}
        elif isinstance(input_data, Protein):
            dic = {k: np.array(v) for k, v in input_data.to_dict('list').items()}
            dic['r_number'] = np.array(input_data.index)
        else:
            raise TypeError("Invalid input data type")

        #todo this does not apply to all data sets? (it does not, for example images)
        if 'color' not in dic.keys() and 'image' not in self.tags:
            column = next(iter(dic.values()))
            color = np.full_like(column, fill_value=self.default_color, dtype='<U7')
            dic['color'] = color
        return dic

    def to_numpy(self):
        raise NotImplementedError('Converting to numpy rec array not implemented')

    @property
    def scalar_fields(self):
        """Returns a list of names of fields with scalar dtype"""
        return [name for name, data in self.source.data.items() if np.issubdtype(data.dtype, np.number)]

    @property
    def y(self):
        """:class:`~numpy.ndarray`: Array of y values"""
        if 'y' in self.render_kwargs:
            try:
                return self.source.data[self.render_kwargs['y']]
            except TypeError:
                return None #  'y' might be a list of y values
        elif 'y' in self.source.data:
            return self.source.data['y']
        else:
            return None

    @property
    def x(self):
        """:class:`~numpy.ndarray`: Array of x values"""
        if 'x' in self.render_kwargs:
            return self.source.data[self.render_kwargs['x']]
        elif 'x' in self.source.data:
            return self.source.data['x']
        else:
            return None

    def update(self, data_source_obj):
        """
        Update the data and source object

        Parameters
        ----------
        data_source

        Returns
        -------


        """

        self.source.data.update(**data_source_obj.source.data)

    def resolve_tags(self, tags):
        # format ['tag1', ('tag2a', 'tag2b') ] = tag1 OR (tag2a AND tag2b)

        for tag in tags:
            if isinstance(tag, str):
                bool = tag in self.tags
                # if tag in self.tags:
                #     return True
            else:
                bool = all(sub_tag in self.tags for sub_tag in tag)
            if bool:
                return True
        return False

    #def _resolve_tag(self, tag):