package ISP;

public class Increment implements Incrementable {

	
	@Override
	public double getIncrementByYear(double years, double afection) {
		// TODO Auto-generated method stub
		double increment=0;
		
		if(years> 45 && years < 65) {
			increment = afection * 0.3;
		}
		if (years > 65)
			increment = afection * 0.5;
		// calculate impact
	
		return increment;
	}
	}

